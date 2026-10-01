"""The data pipeline, working directly on the MongoDB directory (services/store.py).

  IPEDS universities -> official faculty directories -> professors
  -> OpenAlex author match (faculty-page paper first, else name + institution, else UNRESOLVED)
  -> recent papers (last 5 years) + OpenAlex subfields -> (optional) grants -> (optional) hiring

step() does one small unit of work and returns a message; the background worker calls it
in a loop. Every write goes straight to MongoDB, so nothing is lost on a sandbox reset.
"""

import os
import time
import threading

from services import store as st
from services import fetchers as fx
from services import universities as unis

WORK = threading.Lock()
MAX_PER_DEPARTMENT = 200          # safety cap against runaway pages
GRANTS = lambda: os.environ.get("GRANTS_ENABLED", "0") == "1"
FINISHED = ("DONE", "NO_FACULTY_FOUND", "FAILED", "STAFF_REVIEW")
_LOG = {"lines": [], "steps": 0, "last": "", "at": ""}


def log(msg):
    _LOG["steps"] += 1
    _LOG["last"] = msg
    _LOG["at"] = st.now_iso()
    _LOG["lines"] = ([_LOG["at"][11:19] + "  " + msg] + _LOG["lines"])[:60]
    print(f"[pipeline] {msg}")


def log_state():
    return dict(_LOG)


# ---------------- universities ----------------

def _known_dirs():
    """IPEDS id -> [[department, url], ...] from the curated list in universities.py."""
    return {row["ipeds"]: row["dirs"] for row in unis.PRIORITY if row.get("dirs")}


def load_ipeds():
    """Load/refresh every R1 + R2 research university from the IPEDS file."""
    from services import ipeds
    year, rows = ipeds.research_universities()
    known = _known_dirs()
    curated_rank = {row["ipeds"]: i for i, row in enumerate(unis.PRIORITY)}
    n_new = 0
    for r in rows:
        iid = r["ipeds_id"]
        existed = st.get_institution(iid) is not None
        fields = {k: v for k, v in r.items()}
        # curated schools keep their hand-picked order at the front of their tier
        if iid in curated_rank:
            fields["priority_rank"] = curated_rank[iid]
        fields["directories"] = known.get(iid, [])
        st.upsert_institution(iid, fields)
        n_new += 0 if existed else 1
    # curated schools that are not R1/R2 in IPEDS (e.g. Oakland) stay in the queue too
    for row in unis.PRIORITY:
        if st.get_institution(row["ipeds"]) is None:
            st.upsert_institution(row["ipeds"], {
                "ipeds_id": row["ipeds"], "name": row["name"], "city": row["city"], "state": row["state"],
                "official_website": row["web"], "ror_id": row["ror"], "priority_tier": int(row["tier"]),
                "priority_rank": curated_rank[row["ipeds"]], "priority_reason": unis.TIER_REASON[int(row["tier"])],
                "directories": row["dirs"],
            })
            n_new += 1
        else:
            st.update_institution(row["ipeds"], {"directories": row["dirs"], "ror_id": row["ror"]})
    st.set_setting("ipeds_year", year)
    return year, len(rows), n_new


def queue():
    return st.list_institutions()


# ---------------- crawling ----------------

FIELDS_HINT = "computer science, electrical and computer engineering, mechanical, aerospace, biomedical, chemical, civil engineering, robotics, data science, statistics, mathematics, physics, chemistry, biology"


def find_directories(inst):
    """No curated URLs. Returns (dirs, status) where status is FOUND / NONE / STAFF_REVIEW.
      1. deterministic discovery (sitemaps, homepage + academic-unit links, WordPress feed)
      2. LLM URL guesses (three-model chain) - every guess is fetched and validated
      3. all models failed technically -> STAFF_REVIEW (never NO_FACULTY_FOUND)"""
    from services import discovery
    found = discovery.discover(inst, log)
    if found:
        return found, "FOUND"
    ai = _ai()
    if not ai:
        return [], "NONE"
    dom = discovery.domain_of(inst.get("official_website") or "")
    o = ai.guess_directories(inst["name"], dom, FIELDS_HINT)
    if o.status == "STAFF_REVIEW":
        flag_staff_review("FACULTY_DIRECTORY_DISCOVERY", "FACULTY_DIRECTORY_DISCOVERY_FAILED", inst,
                          source_url=inst.get("official_website", ""), outcome=o, extra={"official_domain": dom})
        return [], "STAFF_REVIEW"
    out = []
    for g in (o.value or [])[:10]:
        url = str(g.url)
        if not discovery.on_domain(url, dom):
            continue                                  # off-domain guesses are discarded
        ok, rows, info = discovery.validate(url, str(g.department) or "Faculty")
        if ok:
            out.append(dict(info, department=str(g.department) or discovery._dept_label("", url), url=url,
                            discovery_method="llm_guess", **_ai_meta(o)))
        time.sleep(1)
    return out, ("FOUND" if out else "NONE")


def extract_faculty(url, dept, inst):
    """Rules first (links, titles, "Last, First", WordPress feed); the AI parser only for pages the
    rules cannot read, and every AI name must appear in the fetched text.
    Returns (rows, via, failed_outcome_or_None)."""
    rows, via = fx.extract_faculty_any(url, dept)
    if rows:
        return rows, via, None
    ai = _ai()
    if not ai:
        return [], via, None
    page = fx.fetch_page(url)
    if not page.get("ok"):
        return [], via, None
    o = ai.extract_faculty(page["text"], dept)
    if o.status == "STAFF_REVIEW":
        return [], "ai_failed", o
    out = [{"name": r.name.strip(), "title": r.title or "Professor", "department": dept, "profile_url": url}
           for r in (o.value or []) if "professor" in (r.title or "").lower()]
    return out, f"ai:{o.model_used}", None


def crawl(inst):
    """Import professors from every known directory page of this university.
    Returns (added, per-directory report, status)."""
    dirs = list(inst.get("directories") or [])
    disc_status = "CURATED" if dirs else ""
    if not dirs:
        found, disc_status = find_directories(inst)
        dirs = [[d["department"], d["url"]] for d in found]
        if found:
            st.update_institution(inst["id"], {"directories": dirs, "directory_meta": [dict(d, checked_at=st.now_iso()) for d in found]})
    added = 0
    report = []
    for dept, url in dirs:
        rows, via, failed = extract_faculty(url, dept, inst)
        if not rows and not failed:
            # A bot check can be temporary (it tightens after bursts): wait and try once more.
            time.sleep(20)
            rows, via, failed = extract_faculty(url, dept, inst)
        if failed:
            flag_staff_review("FACULTY_EXTRACTION", "ALL_LLM_FALLBACKS_FAILED", inst, source_url=url, outcome=failed)
        n = 0
        for r in rows[:MAX_PER_DEPARTMENT]:
            if st.add_professor(inst, r["name"], r["title"], r["department"] or dept, r["profile_url"] or url):
                n += 1
        added += n
        report.append({"department": dept, "url": url, "found": len(rows), "added": n, "via": via})
        time.sleep(1.5)   # be gentle with the reader service / university sites
    st.update_institution(inst["id"], {"dirs_checked": report})
    st.recount(inst["id"])
    st.invalidate_search()
    if not dirs and disc_status == "STAFF_REVIEW":
        return added, report, "STAFF_REVIEW"
    return added, report, disc_status


# ---------------- OpenAlex ----------------

def names_match(prof_name, candidate):
    a = st.normalize_name(prof_name).split()
    b = st.normalize_name(candidate).split()
    if not a or not b:
        return False
    if a[-1] == b[-1]:
        if a[0][0] == b[0][0]:
            return True
        for g in b[:-1]:
            if len(a[0]) >= 3 and len(g) >= 3 and (g.startswith(a[0]) or a[0].startswith(g)):
                return True
        return False
    return a[0] == b[-1] and a[-1] == b[0]


def _sid(x):
    return fx.short_id(x or "")


def resolve_institution(inst):
    if inst.get("openalex_institution_id"):
        return inst["openalex_institution_id"]
    found = fx.search_institution(inst["name"], inst.get("ror_id", ""))
    if not found:
        return ""
    oid = _sid(found.get("id"))
    st.update_institution(inst["id"], {"openalex_institution_id": oid, "ror_id": inst.get("ror_id") or _sid(found.get("ror"))})
    inst["openalex_institution_id"] = oid
    return oid


def _affiliated(author, inst_oid):
    for i in author.get("last_known_institutions") or []:
        if _sid(i.get("id")) == inst_oid:
            return True
    for af in author.get("affiliations") or []:
        if _sid((af.get("institution") or {}).get("id")) == inst_oid:
            return True
    return False


def match(p, inst_oid):
    """Returns the fields to store: author id, orcid, match status/method/note."""
    # A/B: faculty-page publications (DOI first, else exact title), at most 3
    for anc in (p.get("anchors") or [])[:3]:
        works, method = [], "DOI"
        if anc.get("doi"):
            w = fx.fetch_work_by_doi(anc["doi"])
            works = [w] if w else []
        if not works and anc.get("title"):
            method = "TITLE"
            target = st.normalize_name(anc["title"])
            works = [c for c in fx.search_work_by_title(anc["title"]) if st.normalize_name(c.get("title") or "") == target][:1]
        for w in works:
            for au in w.get("authorships") or []:
                author = au.get("author") or {}
                aid = _sid(author.get("id"))
                if aid and names_match(p["name"], author.get("display_name") or ""):
                    at_inst = any(_sid(i.get("id")) == inst_oid for i in au.get("institutions") or [])
                    return {"openalex_author_id": aid, "orcid": p.get("orcid") or _sid(author.get("orcid")),
                            "match_status": "MATCHED", "match_method": "PAPER_" + method,
                            "match_note": f"Matched via faculty-page publication \"{anc.get('title', '')}\"; "
                                          + ("institution verified." if at_inst else "institution not verified on that paper.")}
    # C: name + institution; accept only one clear author
    if not inst_oid:
        return {"match_status": "UNRESOLVED", "match_note": "Institution not found in OpenAlex."}
    cands = [a for a in fx.search_authors(p["name"], inst_oid) if names_match(p["name"], a.get("display_name") or "")]
    note = "Matched on name + institution (one clear OpenAlex author)."
    if not cands:
        cands = [a for a in fx.search_authors(p["name"], "") if names_match(p["name"], a.get("display_name") or "") and _affiliated(a, inst_oid)]
        note = "Matched on name + institution in OpenAlex affiliation history."
    if p.get("orcid"):
        by = [a for a in cands if _sid(a.get("orcid")) == p["orcid"]]
        if len(by) == 1:
            cands = by
    if len(cands) > 1:
        cands.sort(key=lambda a: int(a.get("works_count") or 0), reverse=True)
        top = int(cands[0].get("works_count") or 0)
        rest = sum(int(a.get("works_count") or 0) for a in cands[1:])
        if top >= 20 and top >= 5 * max(rest, 1):
            cands = cands[:1]
            note = "Matched on name + institution (dominant OpenAlex profile; smaller duplicates ignored)."
    if len(cands) == 1:
        return {"openalex_author_id": _sid(cands[0].get("id")), "orcid": p.get("orcid") or _sid(cands[0].get("orcid")),
                "match_status": "MATCHED", "match_method": "NAME_INSTITUTION", "match_note": note}
    return {"match_status": "UNRESOLVED", "match_method": "",
            "match_note": "No OpenAlex author for name + institution." if not cands
            else f"Ambiguous: {len(cands)} OpenAlex authors with this name at the institution."}


SCHOLAR_REASONS = {
    "NO_SCHOLAR_PROFILE": "No Google Scholar profile with this name at this university.",
    "SCHOLAR_PROFILE_UNREADABLE": "Google Scholar profile found but its paper list could not be read.",
    "SCHOLAR_AMBIGUOUS": "Google Scholar papers point to more than one OpenAlex author.",
    "SCHOLAR_PAPERS_NOT_IN_OPENALEX": "None of the Google Scholar papers were found in OpenAlex.",
}


def resolve_unmatched(p, inst, inst_oid):
    """OpenAlex name + institution failed: try the professor's Google Scholar profile.
    Still unresolved -> Staff review (OPENALEX_IDENTITY). A later match clears the review item."""
    from services import scholar
    try:
        out, reason = scholar.match_via_scholar(p, inst, inst_oid, names_match)
    except fx.RateLimited:
        raise                                  # OpenAlex resting: the worker waits and retries
    except Exception as e:
        print(f"[pipeline] scholar fallback failed for {p['name']}: {e}")
        return {}
    if out.get("match_status") == "MATCHED":
        st.db().staff_review.delete_many({"task_type": "OPENALEX_IDENTITY", "professor_id": p["id"]})
        return out
    if reason:
        flag_staff_review("OPENALEX_IDENTITY", reason, inst, p, source_url=out.get("scholar_url") or p.get("faculty_url", ""),
                          extra={"last_error": SCHOLAR_REASONS.get(reason, reason),
                                 "openalex_note": p.get("match_note", ""), "department": p.get("department", "")})
    if "match_note" in out:
        out["match_note"] = p.get("match_note", "") + " " + out["match_note"]
    return out


def scholar_backfill_one():
    """Professors left UNRESOLVED before the Google Scholar step existed: give each one Scholar
    check when the worker is otherwise idle. Returns a log message, or "" when nothing to do."""
    from services import websearch as ws
    if not ws.status()["available"]:
        return ""
    doc = st.db().professors.find_one({"match_status": "UNRESOLVED", "scholar_checked": {"$exists": False}})
    if doc is None:
        return ""
    p = st._clean(doc)
    inst = st.get_institution(p["institution_id"])
    if inst is None:
        st.update_professor(p["id"], {"scholar_checked": st.now_iso()})
        return ""
    out = resolve_unmatched(p, inst, resolve_institution(inst))
    if not out:
        return ""                              # search cooled down mid-check; retried later
    if out.get("match_status") == "MATCHED":
        out["pipeline_done"] = False           # papers + subfields are pulled on the next pass
        st.update_institution(inst["id"], {"pipeline_state": "PROCESSING"})
    st.update_professor(p["id"], out)
    st.invalidate_search()
    return f"{p['name']} ({inst['name']}): Google Scholar check -> {out.get('match_status') or 'still unresolved (staff review)'}"


def ingest_works(p):
    """Recent papers (last 5 years) + their OpenAlex subfields. Returns (paper_ids, subfields, fields, award_ids)."""
    year = time.gmtime().tm_year - 4
    ids, subs, flds, awards = [], [], [], []
    for w in fx.fetch_author_works(p["openalex_author_id"], year):
        wid = _sid(w.get("id"))
        if not wid:
            continue
        topic = w.get("primary_topic") or {}
        sub = (topic.get("subfield") or {}).get("display_name") or ""
        fld = (topic.get("field") or {}).get("display_name") or ""
        loc = w.get("primary_location") or {}
        doi = w.get("doi") or ""
        st.upsert_paper({
            "openalex_work_id": wid, "doi": fx.clean_doi(doi) if doi else "",
            "title": w.get("title") or w.get("display_name") or "",
            "publication_date": w.get("publication_date") or "", "publication_year": int(w.get("publication_year") or 0),
            "source_name": ((loc.get("source") or {}).get("display_name")) or "",
            "paper_url": doi or loc.get("landing_page_url") or w.get("id") or "",
            "citation_count": int(w.get("cited_by_count") or 0),
            "subfield": sub, "subfield_id": _sid((topic.get("subfield") or {}).get("id")), "field": fld,
        })
        ids.append(wid)
        if sub and sub not in subs:
            subs.append(sub)
        if fld and fld not in flds:
            flds.append(fld)
        for aw in w.get("awards") or []:
            aid = _sid(aw.get("id"))
            if aid and aid not in awards:
                awards.append(aid)
    return ids, sorted(subs), sorted(flds), awards


def _person_name(person):
    return person.get("display_name") or f"{person.get('given_name', '')} {person.get('family_name', '')}".strip()


def award_role(p, inst, award):
    """PI / CO_PI / INVESTIGATOR when the professor is explicitly on the award, else ""."""
    people = []
    if isinstance(award.get("lead_investigator"), dict):
        people.append(("PI", award["lead_investigator"]))
    if isinstance(award.get("co_lead_investigator"), dict):
        people.append(("CO_PI", award["co_lead_investigator"]))
    people += [("CO_PI", c) for c in award.get("co_lead_investigators") or []]
    people += [("INVESTIGATOR", c) for c in award.get("investigators") or []]
    awarded = _sid((award.get("institution_awarded") or {}).get("id"))
    for role, person in people:
        porcid = _sid(person.get("orcid"))
        if p.get("orcid") and porcid:
            if porcid == p["orcid"]:
                return role
            continue
        if not names_match(p["name"], _person_name(person)):
            continue
        aff = ((person.get("affiliation") or {}).get("name") or "").lower()
        uni = inst["name"].lower()
        if (aff and (aff == uni or uni in aff or aff in uni)) or (awarded and awarded == inst.get("openalex_institution_id")):
            return role
    return ""


def discover_grants(p, inst, award_ids):
    linked = []
    for aid in award_ids[:25]:
        award = fx.fetch_award(aid)
        if not award:
            continue
        role = award_role(p, inst, award)
        if not role:
            continue
        amt = award.get("amount")
        st.upsert_grant({
            "openalex_award_id": aid, "title": award.get("display_name") or "",
            "funder_name": ((award.get("funder") or {}).get("display_name")) or "",
            "funder_award_id": award.get("funder_award_id") or "",
            "amount": float(amt) if isinstance(amt, (int, float)) else 0.0, "currency": award.get("currency") or "",
            "start_date": award.get("start_date") or "", "end_date": award.get("end_date") or "",
            "landing_page_url": award.get("landing_page_url") or "",
        })
        linked.append({"id": aid, "role": role})
    return linked


# ---------------- AI (three-model fallback) + staff review ----------------

def _ai():
    """services/ai.jac (the by-llm tasks with the model fallback chain); None if no model configured."""
    try:
        from services import ai
        return ai if ai.configured() else None
    except Exception as e:
        print(f"[pipeline] AI module unavailable: {e}")
        return None


def _ai_meta(o):
    return {"model_used": o.model_used, "attempt_number": o.attempt_number, "fallback_count": o.fallback_count}


def flag_staff_review(task_type, reason, inst=None, prof=None, source_url="", outcome=None, extra=None):
    """STAFF_REVIEW is a first-class state: the system could not finish the task. It is never
    recorded as NO_FACULTY_FOUND or NO_PUBLIC_SIGNAL_FOUND."""
    key = f"{task_type}:{(prof or {}).get('id') or (inst or {}).get('id', '')}:{source_url}"
    doc = {
        "_id": key, "status": "STAFF_REVIEW", "reason": reason, "task_type": task_type,
        "university": (inst or {}).get("name", ""), "institution_id": (inst or {}).get("id", ""),
        "professor": (prof or {}).get("name"), "professor_id": (prof or {}).get("id"),
        "source_url": source_url,
        "models_attempted": list(outcome.models_attempted) if outcome else [],
        "last_error": (outcome.last_error if outcome else "") or (extra or {}).get("last_error", ""),
        "error_kind": outcome.error_kind if outcome else "", "attempted_at": st.now_iso(), "resolved": False,
    }
    doc.update(extra or {})
    st.db().staff_review.replace_one({"_id": key}, doc, upsert=True)
    log(f"Staff review: {task_type} {reason} - {(prof or {}).get('name') or (inst or {}).get('name', '')}")


def enrich_profile(p, inst):
    """Faculty profile page -> lab URL, homepage, ORCID and up to 3 listed papers (anchors that
    confirm the OpenAlex identity). Without a model this step is skipped."""
    ai = _ai()
    if not ai or p.get("profile_extracted") or not p.get("faculty_url"):
        return {}
    page = fx.fetch_page(p["faculty_url"])
    if not page.get("ok"):
        return {}                     # page unreachable now; try again on the next run
    o = ai.extract_profile(page["text"], p["name"])
    if o.status == "STAFF_REVIEW":
        flag_staff_review("PROFILE_EXTRACTION", "ALL_LLM_FALLBACKS_FAILED", inst, p, p["faculty_url"], o)
        return {}
    out = {"profile_extracted": True, "profile_ai": _ai_meta(o)}
    info = o.value
    if info is None:
        return out
    low = page["text"]
    if info.lab_url and not p.get("lab_url") and info.lab_url.split("//")[-1][:40] in low:
        out["lab_url"] = info.lab_url
    if info.personal_url and not p.get("personal_url") and info.personal_url.split("//")[-1][:40] in low:
        out["personal_url"] = info.personal_url
    if info.orcid and not p.get("orcid") and fx.short_id(info.orcid) in low:
        out["orcid"] = fx.short_id(info.orcid)
    anchors = [{"title": pub.title, "doi": pub.doi, "publication_year": pub.year}
               for pub in (info.publications or [])[:3]
               if pub.title and fx.normalize_quote_text(pub.title)[:60] in fx.normalize_quote_text(low)]
    if anchors and not p.get("anchors"):
        out["anchors"] = anchors
    return out


HIRING_POSITIVE = ("DIRECT_HIRING", "INDIRECT_HIRING", "GENERAL_RECRUITMENT")


def _hiring_on_page(ai, p, inst, url, method):
    """Fetch one page, ask the model, verify the quote on the fetched text.
    Returns ("FOUND"|"UNCERTAIN"|"NONE"|"FAILED", record)."""
    page = fx.fetch_page(url)
    if not page.get("ok"):
        return "NONE", None
    o = ai.extract_hiring(page["text"], p["name"])
    if o.status == "STAFF_REVIEW":
        return "FAILED", o
    if o.status == "VALID_EMPTY":
        return "NONE", None
    f = o.value
    if not fx.quote_on_page(f.quote, page["text"]):
        return "NONE", None           # the model's sentence is not on the page: rejected
    rec = {"status": f.status.name, "quote": f.quote.strip(), "source_url": page.get("url") or url,
           "source_title": page.get("title") or "", "page_verified": True, "discovery_method": method,
           "confidence": round(float(f.confidence or 0), 2), **_ai_meta(o)}
    return ("UNCERTAIN" if f.status.name == "UNCERTAIN" else "FOUND"), rec


def check_hiring(p, inst):
    """Hiring signal, grounded in pages we fetch ourselves:
      1. the professor's lab / personal / faculty pages
      2. if nothing there: web search (DDGS) -> ranked candidate URLs -> same check
    Result status: DIRECT_HIRING / INDIRECT_HIRING / GENERAL_RECRUITMENT / UNCERTAIN /
    NO_PUBLIC_SIGNAL_FOUND. Returns None when it could not run (no model, search paused) so the
    professor keeps their previous result and is re-checked later."""
    ai = _ai()
    if not ai:
        return None
    ts = st.now_iso()
    uncertain, failures = None, []
    known = [(p.get("lab_url"), "lab_page"), (p.get("personal_url"), "personal_page"), (p.get("faculty_url"), "faculty_page")]
    seen = set()
    for url, method in known:
        if not url or url in seen:
            continue
        seen.add(url)
        kind, rec = _hiring_on_page(ai, p, inst, url, method)
        if kind == "FOUND":
            return dict(rec, checked_at=ts, date_found=ts)
        if kind == "UNCERTAIN" and uncertain is None:
            uncertain = rec
        if kind == "FAILED":
            failures.append((url, rec))
    # 2. web search for pages we don't know yet
    from services import websearch
    from services import discovery
    domain = discovery.domain_of(inst.get("official_website") or "")
    try:
        cands = websearch.find_hiring_candidates(p["name"], domain)
    except websearch.SearchUnavailable as e:
        log(f"Search paused ({str(e)[:80]}); {p['name']} hiring re-checked later.")
        if uncertain:
            return dict(uncertain, checked_at=ts)
        return None
    for url in cands:
        if url in seen:
            continue
        seen.add(url)
        kind, rec = _hiring_on_page(ai, p, inst, url, "search_result")
        if kind == "FOUND":
            return dict(rec, checked_at=ts, date_found=ts)
        if kind == "UNCERTAIN" and uncertain is None:
            uncertain = rec
        if kind == "FAILED":
            failures.append((url, rec))
    if uncertain:
        return dict(uncertain, checked_at=ts)
    if failures:
        url, o = failures[0]
        flag_staff_review("HIRING_EXTRACTION", "ALL_LLM_FALLBACKS_FAILED", inst, p, url, o)
        return {"status": "STAFF_REVIEW", "quote": "", "source_url": url, "checked_at": ts}
    return {"status": "NO_PUBLIC_SIGNAL_FOUND", "quote": "", "source_url": "", "checked_at": ts,
            "pages_checked": len(seen)}


def process_professor(p, inst):
    inst_oid = resolve_institution(inst)
    fields = enrich_profile(p, inst)
    p = dict(p, **fields)
    if not p.get("openalex_author_id"):
        fields.update(match(p, inst_oid))
        if fields.get("match_status") == "UNRESOLVED":
            fields.update(resolve_unmatched(dict(p, **fields), inst, inst_oid))
    author = fields.get("openalex_author_id") or p.get("openalex_author_id")
    n_papers = len(p.get("paper_ids") or [])
    grants = p.get("grants") or []
    if author:
        q = dict(p, openalex_author_id=author)
        ids, subs, flds, awards = ingest_works(q)
        fields.update(paper_ids=ids, subfields=subs, fields=flds, last_openalex_update=st.now_iso())
        n_papers = len(ids)
        if GRANTS():
            grants = discover_grants(dict(q, orcid=fields.get("orcid") or p.get("orcid")), inst, awards)
            fields.update(grants=grants, grant_count=len(grants), last_grant_update=st.now_iso())
    h = check_hiring(dict(p, **fields), inst)
    if h is not None:
        if h["status"] == "STAFF_REVIEW":
            fields.update(last_hiring_update=h["checked_at"])     # keep the previous verified result
        else:
            fields.update(hiring=h, has_hiring=h["status"] in HIRING_POSITIVE, hiring_status=h["status"],
                          last_hiring_update=h["checked_at"])
    fields["pipeline_done"] = True
    merged = dict(p, **fields)
    fields["search_text"] = st.search_text_for(merged) + " | " + st.normalize_name(inst.get("city", "") + " " + inst.get("state", ""))
    st.update_professor(p["id"], fields)
    return f"{p['name']} ({inst['name']}): {merged.get('match_status')}, {n_papers} papers" + (f", {len(grants)} grants" if GRANTS() else "")


# ---------------- scheduler ----------------

def step():
    """One unit of work. Order: finish matching universities already imported, else crawl the
    next queued university. Raises fx.RateLimited when OpenAlex is resting (caller waits)."""
    with WORK:
        if st.count_institutions() == 0:
            year, n, new = load_ipeds()
            return f"Loaded {n} research universities from IPEDS {year}."
        insts = queue()
        limited = bool(fx.rate_limit_status()["limited"])
        if not limited:
            for inst in insts:
                if inst.get("pipeline_state") != "PROCESSING":
                    continue
                p = st.next_pending_professor(inst["id"])
                if p is None:
                    st.update_institution(inst["id"], {"pipeline_state": "DONE"})
                    st.recount(inst["id"])
                    return f"{inst['name']}: all professors processed."
                msg = process_professor(p, inst)
                if st.count_professors({"institution_id": inst["id"], "pipeline_done": False}) % 10 == 0:
                    st.recount(inst["id"])
                st.invalidate_search()
                return msg
        for inst in insts:
            if inst.get("pipeline_state") not in ("QUEUED", "CRAWLING"):
                continue
            try:
                added, report, disc = crawl(inst)
            except Exception as e:
                st.update_institution(inst["id"], {"pipeline_state": "FAILED", "pipeline_note": str(e)[:300]})
                return f"{inst['name']}: crawl failed ({str(e)[:150]})"
            total = st.count_professors({"institution_id": inst["id"]})
            if total == 0:
                if disc == "STAFF_REVIEW" or any(r.get("via") == "ai_failed" for r in report):
                    # the system could not finish (every model failed): not the same as "no faculty"
                    st.update_institution(inst["id"], {"pipeline_state": "STAFF_REVIEW",
                                                       "pipeline_note": "AI fallbacks failed; listed under Staff review."})
                    return f"{inst['name']}: needs staff review."
                note = ("No faculty directory could be read." if inst.get("directories") or report
                        else "No faculty directory found (sitemaps, homepage links" + (", AI guesses" if _ai() else "") + "). Add URLs in services/universities.py.")
                st.update_institution(inst["id"], {"pipeline_state": "NO_FACULTY_FOUND", "pipeline_note": note})
                return f"{inst['name']}: no faculty found."
            st.update_institution(inst["id"], {"pipeline_state": "PROCESSING", "pipeline_note": ""})
            return f"{inst['name']}: imported {added} professors from {len(report)} directory pages."
        if limited:
            raise fx.RateLimited(fx.rate_limit_status()["message"])
        msg = scholar_backfill_one()
        if msg:
            return msg
        if maintenance_due():
            n = start_maintenance()
            return f"Monthly maintenance started: re-checking {n} universities (faculty lists, papers, hiring)."
        return ""


MAINTENANCE_DAYS = lambda: int(os.environ.get("MAINTENANCE_DAYS", "30") or 30)


def maintenance_due():
    last = st.get_setting("last_maintenance", "")
    if not last:
        # first full pass is still running (or never finished): start the 30-day clock at that point
        if not st.get_setting("first_pass_done"):
            if st.count_institutions({"pipeline_state": {"$in": ["QUEUED", "CRAWLING", "PROCESSING"]}}) == 0:
                st.set_setting("first_pass_done", st.now_iso())
                st.set_setting("last_maintenance", st.now_iso())
            return False
        return False
    try:
        age = time.time() - time.mktime(time.strptime(last[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return True
    return age > MAINTENANCE_DAYS() * 86400


def start_maintenance():
    """Monthly refresh: re-read every faculty directory (new hires, departures), then re-run
    papers, subfields and hiring for every professor. Existing data stays visible meanwhile."""
    st.set_setting("maintenance_started", st.now_iso())
    n = 0
    for inst in queue():
        if inst.get("pipeline_state") in ("DONE", "PROCESSING", "NO_FACULTY_FOUND"):
            st.update_institution(inst["id"], {"pipeline_state": "CRAWLING"})
            n += 1
    st.db().professors.update_many({}, {"$set": {"pipeline_done": False}})
    st.set_setting("last_maintenance", st.now_iso())
    return n


def requeue(states=("NO_FACULTY_FOUND", "FAILED")):
    n = 0
    for inst in queue():
        if inst.get("pipeline_state") in states:
            st.update_institution(inst["id"], {"pipeline_state": "QUEUED", "pipeline_note": ""})
            n += 1
    return n


def recrawl_thin(min_found=5):
    """Re-queue universities where at least one known directory page yielded fewer than
    `min_found` professors (bot checks, temporary errors). Existing professors are kept."""
    n = 0
    for inst in queue():
        report = inst.get("dirs_checked") or []
        if report and any(int(r.get("found", 0)) < min_found for r in report) and inst.get("pipeline_state") in ("PROCESSING", "DONE", "NO_FACULTY_FOUND"):
            st.update_institution(inst["id"], {"pipeline_state": "CRAWLING"})
            n += 1
    return n


def recrawl_all():
    """Re-read every directory (new departments / fixed parsers); existing professors keep their data."""
    n = 0
    for inst in queue():
        if inst.get("pipeline_state") in ("PROCESSING", "DONE", "NO_FACULTY_FOUND", "FAILED"):
            st.update_institution(inst["id"], {"pipeline_state": "CRAWLING", "pipeline_note": ""})
            n += 1
    return n


def check_directories(limit=None):
    """Directory health report: for each known directory URL, how many professors it yields."""
    out = []
    for inst in queue():
        for dept, url in (inst.get("directories") or []):
            rows, via = fx.extract_faculty_any(url, dept)
            out.append({"university": inst["name"], "department": dept, "url": url, "found": len(rows), "via": via})
            if limit and len(out) >= limit:
                return out
            time.sleep(1)
    return out


# ---------------- background worker ----------------

WORKER = {"running": False, "thread": None}
LAST_SEARCH = {"t": 0.0}


def note_search():
    LAST_SEARCH["t"] = time.time()


def _loop():
    time.sleep(5)
    while WORKER["running"]:
        # visitors first: pause while someone searched in the last 20 s
        while WORKER["running"] and time.time() - LAST_SEARCH["t"] < 20:
            time.sleep(2)
        try:
            msg = step()
            if msg:
                log(msg)
                time.sleep(1)
            else:
                log("All universities processed.")
                time.sleep(600)
        except fx.RateLimited as e:
            log(f"Waiting for OpenAlex rate limit ({e}); crawling continues when possible.")
            time.sleep(300)
        except Exception as e:
            log(f"Step failed: {str(e)[:200]}")
            time.sleep(20)


def start():
    WORKER["running"] = True
    st.set_setting("worker_running", True)
    t = WORKER.get("thread")
    if t is None or not t.is_alive():
        WORKER["thread"] = threading.Thread(target=_loop, daemon=True)
        WORKER["thread"].start()


def stop():
    WORKER["running"] = False
    st.set_setting("worker_running", False)


def running():
    return WORKER["running"]
