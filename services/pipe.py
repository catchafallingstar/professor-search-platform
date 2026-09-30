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
FINISHED = ("DONE", "NO_FACULTY_FOUND", "FAILED")
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

def _guess_directories(inst):
    """No curated URLs: ask the LLM (when configured) for directory pages on the school's domain."""
    try:
        from services import crawl_llm
        return crawl_llm.guess(inst["name"], inst.get("official_website", ""))
    except Exception as e:
        print(f"[pipeline] directory guess failed for {inst['name']}: {e}")
        return []


def crawl(inst):
    """Import professors from every known directory page of this university.
    Returns (added, per-directory report)."""
    dirs = list(inst.get("directories") or [])
    if not dirs:
        dirs = _guess_directories(inst)
        if dirs:
            st.update_institution(inst["id"], {"directories": dirs})
    added = 0
    report = []
    for dept, url in dirs:
        rows, via = fx.extract_faculty_any(url, dept)
        if not rows:
            # A bot check can be temporary (it tightens after bursts): wait and try once more.
            time.sleep(20)
            rows, via = fx.extract_faculty_any(url, dept)
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
    return added, report


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


def _llm_mods():
    """The Jac by-llm modules (crawler.jac, hiring.jac); None when no LLM is configured."""
    if not fx.llm_configured():
        return None
    try:
        from services import crawler, hiring
        return crawler, hiring
    except Exception as e:
        print(f"[pipeline] LLM modules unavailable: {e}")
        return None


def enrich_profile(p):
    """Faculty profile page -> lab URL, homepage, ORCID and up to 3 listed papers (anchors
    used to confirm the OpenAlex identity). Needs the LLM; skipped silently without it."""
    mods = _llm_mods()
    if not mods or p.get("profile_extracted") or not p.get("faculty_url"):
        return {}
    crawler = mods[0]
    page = crawler.fetch_page(p["faculty_url"])
    info = crawler.extract_profile(page, p["name"])
    out = {"profile_extracted": True}
    if info is None:
        return out
    if info.lab_url and not p.get("lab_url"):
        out["lab_url"] = info.lab_url
    if info.personal_url and not p.get("personal_url"):
        out["personal_url"] = info.personal_url
    if info.orcid and not p.get("orcid"):
        out["orcid"] = fx.short_id(info.orcid)
    anchors = [{"title": pub.title, "doi": pub.doi, "publication_year": pub.year, "url": pub.url}
               for pub in (info.publications or [])[:3] if pub.title]
    if anchors and not p.get("anchors"):
        out["anchors"] = anchors
    return out


def check_hiring(p, inst):
    """Hiring statement: the professor's own pages first (quote must be verbatim on the fetched
    page), then the research LLM (accepted only if the quote is found on its source page).
    Returns the hiring dict to store; quote "" means "no current statement found"."""
    mods = _llm_mods()
    if not mods:
        return None
    crawler, hiring = mods
    ts = st.now_iso()
    for url in (p.get("lab_url"), p.get("personal_url"), p.get("faculty_url")):
        if not url:
            continue
        page = crawler.fetch_page(url)
        q = crawler.find_hiring_quote(page, p["name"])
        if q:
            return {"quote": q, "source_url": page.url, "source_title": page.title or "Faculty page",
                    "date_found": ts, "last_checked": ts}
    res = hiring.research_hiring(p["name"], inst["name"], p.get("department", ""), p.get("faculty_url", ""), p.get("lab_url", ""))
    if res.quote and res.source_url:
        src = crawler.fetch_page(res.source_url)
        if crawler.quote_on_page(res.quote, src):
            return {"quote": res.quote, "source_url": src.url, "source_title": res.source_title or src.title,
                    "date_found": ts, "last_checked": ts}
    old = p.get("hiring") or {}
    return {"quote": "", "source_url": "", "source_title": "", "date_found": old.get("date_found", ""), "last_checked": ts}


def process_professor(p, inst):
    inst_oid = resolve_institution(inst)
    fields = enrich_profile(p)
    p = dict(p, **fields)
    if not p.get("openalex_author_id"):
        fields.update(match(p, inst_oid))
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
        fields.update(hiring=h, has_hiring=bool(h["quote"]), last_hiring_update=h["last_checked"])
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
                added, report = crawl(inst)
            except Exception as e:
                st.update_institution(inst["id"], {"pipeline_state": "FAILED", "pipeline_note": str(e)[:300]})
                return f"{inst['name']}: crawl failed ({str(e)[:150]})"
            total = st.count_professors({"institution_id": inst["id"]})
            if total == 0:
                note = ("No faculty directory could be read." if inst.get("directories") or report
                        else "No faculty directory URLs known yet (add them in services/universities.py, or set an LLM key to discover them).")
                st.update_institution(inst["id"], {"pipeline_state": "NO_FACULTY_FOUND", "pipeline_note": note})
                return f"{inst['name']}: no faculty found."
            st.update_institution(inst["id"], {"pipeline_state": "PROCESSING", "pipeline_note": ""})
            return f"{inst['name']}: imported {added} professors from {len(report)} directory pages."
        if limited:
            raise fx.RateLimited(fx.rate_limit_status()["message"])
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
