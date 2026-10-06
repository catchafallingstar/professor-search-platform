"""The data pipeline, working directly on the MongoDB directory (services/store.py).

  IPEDS universities -> official faculty directories -> professors
  -> OpenAlex author match (faculty-page paper first, else name + institution, else UNRESOLVED)
  -> recent papers (last 5 years) + OpenAlex subfields -> (optional) grants -> (optional) hiring

step() does one small unit of work and returns a message; the background worker calls it
in a loop. Every write goes straight to MongoDB, so nothing is lost on a sandbox reset.
"""

import os
import re
import time
import urllib.parse
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

FIELDS_HINT = ("all academic departments: engineering and computing; natural sciences and mathematics; "
               "humanities (English, history, philosophy, languages and literatures, classics, religion, art history); "
               "arts (music, theatre, film, visual art); social sciences (economics, political science, sociology, "
               "anthropology, psychology, linguistics, communication); education, law, business, public policy")


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


def more_from_profiles(url, dept, rows, max_pages=200):
    """A listing that pages with JavaScript only shows its first screen when read. If the page is
    paginated, read every profile page under the same folder from the sitemap instead: the name
    comes from the page title, the title (Professor...) from the text after the name."""
    from services import discovery
    page = fx.fetch_page(url)
    if not page.get("ok") or not discovery.looks_paginated(page.get("text", "")):
        return []
    have = {st.normalize_name(r["name"]) for r in rows}
    have_urls = {r.get("profile_url") for r in rows}
    extra = []
    profiles = discovery.sitemap_profiles(url)
    log(f"{url}: listing is paginated; reading {min(len(profiles), max_pages)} profile pages from the sitemap")
    for purl in profiles[:max_pages]:
        if purl in have_urls:
            continue
        pg = fx.fetch_page(purl)
        if not pg.get("ok"):
            continue
        name = re.split(r"\s+[|\-–]\s+", (pg.get("title") or "").strip())[0].strip()
        if not st.looks_like_person(name) or st.normalize_name(name) in have:
            continue
        title = title_from_page(pg.get("text", ""), name)
        if not title:
            continue                            # staff without a professor title are skipped
        have.add(st.normalize_name(name))
        extra.append({"name": name, "title": title, "department": dept, "profile_url": purl})
        time.sleep(0.5)
    return extra


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
        rows = rows + more_from_profiles(url, dept, rows)
        n = 0
        for r in rows[:MAX_PER_DEPARTMENT]:
            if _NOT_CORE_TITLE.search(r.get("title") or ""):
                continue        # adjunct / visiting / emeritus / affiliate: not this university's core faculty
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
        if p.get("anchors"):
            # the faculty page listed papers but none resolved to this author: weaker evidence, say so
            note += " Faculty-page papers were checked first but none were found under this author in OpenAlex."
        return {"openalex_author_id": _sid(cands[0].get("id")), "orcid": p.get("orcid") or _sid(cands[0].get("orcid")),
                "match_status": "MATCHED", "match_method": "NAME_INSTITUTION", "match_note": note}
    return {"match_status": "UNRESOLVED", "match_method": "",
            "match_note": "No OpenAlex author for name + institution." if not cands
            else f"Ambiguous: {len(cands)} OpenAlex authors with this name at the institution."}


# ---------- identity sanity gate (runs after every OpenAlex match) ----------

# department keyword -> OpenAlex fields that fit it. A department missing here is not checked.
_DEPT_FIELDS = [
    (("econom", "finance", "business", "management", "accounting", "marketing", "operations"),
     {"Economics, Econometrics and Finance", "Business, Management and Accounting", "Decision Sciences",
      "Social Sciences", "Mathematics", "Computer Science"}),
    (("political", "sociolog", "public policy", "anthropolog", "communication"),
     {"Social Sciences", "Economics, Econometrics and Finance", "Arts and Humanities", "Psychology",
      "Decision Sciences", "Computer Science", "Mathematics"}),
    (("english", "history", "art", "literature", "language", "philosoph", "classics", "music", "french",
      "german", "romance", "slavic", "asian", "religio", "linguist", "comparative lit", "film"),
     {"Arts and Humanities", "Social Sciences", "Psychology"}),
    (("mathematic", "statistic", "actuarial"),
     {"Mathematics", "Computer Science", "Decision Sciences", "Physics and Astronomy", "Engineering",
      "Economics, Econometrics and Finance", "Biochemistry, Genetics and Molecular Biology",
      "Medicine", "Environmental Science", "Earth and Planetary Sciences", "Neuroscience"}),
    (("computer", "informatics", "information"),
     {"Computer Science", "Engineering", "Mathematics", "Decision Sciences", "Social Sciences",
      "Medicine", "Neuroscience", "Psychology"}),
    (("electrical", "ece", "mechanical", "aerospace", "civil", "industrial", "materials", "nuclear"),
     {"Engineering", "Physics and Astronomy", "Materials Science", "Computer Science", "Mathematics",
      "Energy", "Chemistry", "Environmental Science", "Chemical Engineering"}),
]


def _dept_fields(dept):
    d = (dept or "").lower()
    for keys, allowed in _DEPT_FIELDS:
        if any(k in d for k in keys):
            return allowed
    return None


def identity_check(p, author_id):
    """Is this OpenAlex author really this professor? Returns (ok, note).
    Rejects: (1) works mostly outside the department's fields (Ed Cho, Economics -> cancer biology);
    (2) the same author already linked to a DIFFERENT person at the same university
    (Licheng Liu vs Lihong Liu share A5100396472). Joint appointments of the same person pass."""
    # Two signals must BOTH be bad to reject (either alone misfires: engineers publish in medicine,
    # people move universities): few recent works written at this university, AND few in fields
    # that fit the department. A wrong same-name person fails both (Ed Cho: cancer papers, other
    # institutions); a real professor passes at least one (Erin Cech: social science, but at Michigan).
    inst = st.get_institution(p.get("institution_id", "")) or {}
    inst_oid = inst.get("openalex_institution_id", "")
    works = clean_works(fx.fetch_author_works(author_id, time.gmtime().tm_year - 6), 40)
    uni = inst.get("name", "this university")

    def at_here(w):
        for au in w.get("authorships") or []:
            if _sid((au.get("author") or {}).get("id")) == author_id:
                return any(_sid(i.get("id")) == inst_oid for i in au.get("institutions") or [])
        return False

    if inst_oid and len(works) >= 8:
        flags = [at_here(w) for w in works]          # works are newest first
        at_inst = sum(flags) / len(flags)
        recent_here = any(flags[:5])                  # a recent hire: newest papers already list us
        allowed = _dept_fields(p.get("department"))
        fields = [((w.get("primary_topic") or {}).get("field") or {}).get("display_name") or "" for w in works]
        fields = [f for f in fields if f]
        fit = (sum(1 for f in fields if f in allowed) / len(fields)) if (allowed and fields) else 1.0
        if at_inst < 0.1 and not recent_here:
            return False, (f"OpenAlex author {author_id}: only {round(at_inst * 100)}% of {len(works)} recent works list "
                           f"{uni}, none of the newest; likely a different person with the same name.")
        if at_inst < 0.25 and fit < 0.5:
            return False, (f"OpenAlex author {author_id}: only {round(at_inst * 100)}% of {len(works)} recent works list "
                           f"{uni} and {round(fit * 100)}% fit {p.get('department') or 'the department'}; "
                           "likely a different person with the same name.")
    elif inst_oid:
        # too few recent scholarly works to judge by papers: OpenAlex's current affiliation must be us
        author = fx.fetch_author(author_id) or {}
        current = [_sid(i.get("id")) for i in author.get("last_known_institutions") or []]
        if current and inst_oid not in current and not any(at_here(w) for w in works):
            names = ", ".join(i.get("display_name", "") for i in (author.get("last_known_institutions") or [])[:2])
            return False, (f"OpenAlex author {author_id}: few recent works, none at {uni}, and OpenAlex lists "
                           f"the author at {names}; likely a different person with the same name.")
    for other in st.db().professors.find({"openalex_author_id": author_id, "_id": {"$ne": p["id"]}},
                                         {"name": 1, "institution_id": 1}):
        if not names_match(p["name"], other.get("name", "")):
            return False, f"OpenAlex author {author_id} is already linked to {other.get('name')} (a different person)."
    return True, ""


def gated(p, fields):
    """Apply identity_check to a MATCHED result; a failed check becomes UNRESOLVED (never a wrong match)."""
    aid = fields.get("openalex_author_id")
    if fields.get("match_status") != "MATCHED" or not aid:
        return fields
    ok, note = identity_check(p, aid)
    if ok:
        return fields
    return {"openalex_author_id": "", "orcid": p.get("orcid", ""), "match_status": "UNRESOLVED",
            "match_method": "", "match_note": "Identity check failed: " + note, "rejected_author_id": aid}


# ---------- current affiliation (is this person still core faculty HERE?) ----------

_NOT_CORE_TITLE = re.compile(r"\b(adjunct|visiting|emerit(us|a)|affiliate[ds]?|courtesy|honorary|retired|former)\b", re.I)
_TITLE_ON_PAGE = re.compile(
    r"((?:(?:adjunct|clinical|visiting|research|teaching|affiliate|courtesy|emerit(?:us|a)|associate|assistant|"
    r"distinguished|full|endowed)\s+){0,4}professor(?:\s+emerit(?:us|a))?)", re.I)


def title_from_page(page_text, name):
    """The professor's own profile page title ("Adjunct Clinical Assistant Professor"), read from the
    text just after their name. Directory lists often shorten it to "Assistant Professor"."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", page_text or "")
    t = " ".join(t.split())
    last = (name.split() or [""])[-1]
    for m in re.finditer(re.escape(last), t):
        hit = _TITLE_ON_PAGE.search(t[m.end():m.end() + 160])
        if hit:
            return " ".join(w.capitalize() if w.islower() else w for w in hit.group(1).split())
    return ""


def _uni_core(name):
    return re.split(r"\s*[-,]\s*", name or "")[0].strip().lower()


def check_affiliation(p, inst, page_text=""):
    """Returns {"affiliation_status", "affiliation_note", "title"?}.
    CURRENT      default; or recent evidence ties the person to this university
    NOT_CORE     the university's own page calls them adjunct / visiting / emeritus / affiliate
    LIKELY_MOVED newest papers (OpenAlex affiliations + the raw affiliation text on each paper) list
                 another organisation and none list this university, or OpenAlex's affiliation
                 history ends here years ago while continuing elsewhere.
    Evidence is kept in the note; nothing is deleted."""
    out = {"affiliation_status": "CURRENT", "affiliation_note": "", "affiliation_checked": st.now_iso()}
    page_title = title_from_page(page_text, p.get("name", "")) if page_text else ""
    if page_title:
        out["title"] = page_title
        if _NOT_CORE_TITLE.search(page_title):
            out.update(affiliation_status="NOT_CORE",
                       affiliation_note=f"The university's own profile page lists the title \"{page_title}\".")
            return out
    aid = p.get("openalex_author_id")
    inst_oid = inst.get("openalex_institution_id", "")
    if not aid:
        return out
    now = time.gmtime().tm_year
    uni = _uni_core(inst.get("name", ""))
    recent = clean_works(fx.fetch_author_works(aid, now - 2), 15)
    here, elsewhere = 0, []
    for w in recent:
        for au in w.get("authorships") or []:
            if _sid((au.get("author") or {}).get("id")) != aid:
                continue
            ids = [_sid(i.get("id")) for i in au.get("institutions") or []]
            raw = " | ".join(au.get("raw_affiliation_strings") or [])
            names = [i.get("display_name", "") for i in au.get("institutions") or []]
            if (inst_oid and inst_oid in ids) or (uni and uni in raw.lower()):
                here += 1
            elif names or raw.strip():
                elsewhere.append((w.get("publication_year"), names[0] if names else raw.split(",")[0].strip()))
            break
    if here:
        return out
    if len(elsewhere) >= 2:
        orgs = []
        for _y, o in elsewhere:
            if o and o not in orgs:
                orgs.append(o)
        out.update(affiliation_status="LIKELY_MOVED",
                   affiliation_note=f"None of the {len(elsewhere)} papers since {now - 2} list {inst.get('name')}; "
                                    f"they list {', '.join(orgs[:3])}.")
        return out
    author = fx.fetch_author(aid) or {}
    last_here, last_other, other = 0, 0, ""
    for af in author.get("affiliations") or []:
        years = af.get("years") or []
        if not years:
            continue
        iid = _sid((af.get("institution") or {}).get("id"))
        if iid == inst_oid:
            last_here = max(last_here, max(years))
        elif max(years) > last_other:
            last_other, other = max(years), (af.get("institution") or {}).get("display_name", "")
    if last_here and last_here <= now - 4 and last_other > last_here:
        out.update(affiliation_status="LIKELY_MOVED",
                   affiliation_note=f"OpenAlex lists {inst.get('name')} only until {last_here}, then {other} ({last_other}).")
    return out


def find_current_page(p, inst):
    """For someone who seems to have left: a university profile page elsewhere that names them
    with a faculty title (e.g. be.ucsd.edu for Aadeel Akhtar). Returns (url, title) or ("", "")."""
    from services import websearch, discovery
    home = discovery.domain_of(inst.get("official_website") or "")
    home_root = ".".join(home.split(".")[-2:]) if home else ""
    name = p.get("name", "")
    try:
        rows = websearch.search(f'"{name}" professor {p.get("department", "")}'.strip(), 10)
    except websearch.SearchUnavailable:
        return "", ""
    full = st.normalize_name(name)
    for r in rows:
        host = urllib.parse.urlparse(r["url"]).netloc.lower()
        if not host.endswith(".edu") or (home_root and host.endswith(home_root)):
            continue
        # exact full name only: "Adeel Akhtar" at NJIT is not "Aadeel Akhtar"
        blob = st.normalize_name(r.get("title", "") + " " + r.get("snippet", ""))
        if full and full in blob:
            page = fx.fetch_page(r["url"])
            if page.get("ok") and full in st.normalize_name(page.get("text", "")):
                title = title_from_page(page["text"], name)
                if title:
                    return page.get("url") or r["url"], title
    return "", ""


def affiliation_fields(p, inst, page_text=None):
    """check_affiliation + (when the person seems gone) a lead to their current page.
    A NOT_CORE / LIKELY_MOVED professor stays in the database but leaves default search, and staff
    get a review item with the evidence."""
    if page_text is None:
        page_text = (fx.fetch_page(p["faculty_url"]).get("text", "") if p.get("faculty_url") else "")
    out = check_affiliation(p, inst, page_text)
    if out["affiliation_status"] != "CURRENT":
        url, title = find_current_page(p, inst)
        if url:
            out["current_page_url"], out["current_page_title"] = url, title
            out["affiliation_note"] += f" Possible current page: {url} ({title})."
        flag_staff_review("AFFILIATION", out["affiliation_status"], inst, p, p.get("faculty_url", ""),
                          extra={"last_error": out["affiliation_note"], "current_page_url": out.get("current_page_url", "")})
    return out


SCHOLAR_REASONS = {
    "NO_SCHOLAR_PROFILE": "No Google Scholar profile with this name at this university.",
    "SCHOLAR_PROFILE_UNREADABLE": "Google Scholar profile found but its paper list could not be read.",
    "SCHOLAR_AMBIGUOUS": "Google Scholar papers point to more than one OpenAlex author.",
    "SCHOLAR_PAPERS_NOT_IN_OPENALEX": "None of the Google Scholar papers were found in OpenAlex.",
}


def _llm_papers_to_author(p, inst, papers):
    """Each LLM-suggested work must exist in OpenAlex (DOI, else exact title) with an author whose
    name matches the professor. Returns (openalex_author_id or "", verified titles)."""
    from services import scholar
    votes, verified = {}, []
    for k in papers[:6]:
        works = []
        if k.doi:
            try:
                w = fx.fetch_work_by_doi(k.doi)
                works = [w] if w else []
            except fx.RateLimited:
                raise
            except Exception:
                works = []
        if not works and k.title:
            target = scholar._key(k.title)
            works = [w for w in fx.search_work_by_title(k.title) if scholar._key(w.get("title") or "") == target][:1]
        for w in works:
            for au in w.get("authorships") or []:
                a = au.get("author") or {}
                aid = _sid(a.get("id"))
                if aid and scholar._names_ok(names_match, p["name"], a.get("display_name") or ""):
                    votes[aid] = votes.get(aid, 0) + 1
                    verified.append(k.title)
                    break
    if not votes:
        return "", verified
    best = max(votes, key=votes.get)
    return (best if list(votes.values()).count(votes[best]) == 1 else ""), verified


def _uni_core(name):
    return re.split(r"\s*[-,]\s*", name or "")[0].strip()


def openalex_candidates(p, limit=5):
    """Real OpenAlex author records with this name (any institution), each with its institution
    history, top topics and a few recent work titles - the only options the local model may pick."""
    out = []
    for a in fx.search_authors(p["name"], "")[:6]:
        if not names_match(p["name"], a.get("display_name") or ""):
            continue
        insts = [i.get("display_name") for i in a.get("last_known_institutions") or [] if i.get("display_name")]
        for af in a.get("affiliations") or []:
            n = (af.get("institution") or {}).get("display_name")
            if n and n not in insts:
                insts.append(n)
        topics = [t.get("display_name") for t in (a.get("topics") or [])[:2] if t.get("display_name")]
        out.append({"id": _sid(a.get("id")), "name": a.get("display_name") or "", "works_count": int(a.get("works_count") or 0),
                    "institutions": insts[:6], "topics": topics, "works": []})
        if len(out) >= limit:
            break
    for c in out:                                   # a few recent work titles per candidate
        try:
            c["works"] = [(w.get("title") or "")[:70] for w in fx.fetch_author_works(c["id"], 2015)[:2] if w.get("title")]
        except fx.RateLimited:
            raise
        except Exception:
            c["works"] = []
    return out


def _local_llm_pick(p, inst):
    """Retrieve-then-verify with the local model. Returns (fields, reason) or raises LocalLLMError."""
    from services import llm_local
    cands = openalex_candidates(p)
    meta = {"model_used": os.environ.get("LLM_MODEL", "").strip(), "attempt_number": 1, "fallback_count": 0,
            "candidates": [{"id": c["id"], "institutions": c["institutions"][:3]} for c in cands]}
    base = {"llm_papers_checked": st.now_iso()}
    if not cands:
        return dict(base, llm_papers_ai=meta, match_note="No OpenAlex author with this name at all."), "NO_RESULT_FOUND"
    ans = llm_local.pick_candidate({"name": p["name"], "title": p.get("title", ""), "department": p.get("department", ""),
                                    "university": inst.get("name", "")}, cands)
    meta.update(choice=ans["choice"], confidence=ans["confidence"], reason=ans["reason"])
    base["llm_papers_ai"] = meta
    idx = ord(ans["choice"][0]) - 65 if ans["choice"] and ans["choice"] != "NONE" and ans["choice"][0].isalpha() else -1
    if idx < 0 or idx >= len(cands) or ans["confidence"] < 0.7:
        return dict(base, match_note=f"The AI ({meta['model_used']}) did not pick any of {len(cands)} OpenAlex candidates: {ans['reason']}"), "NO_RESULT_FOUND"
    c = cands[idx]
    # safety check: the pick must really carry this university in its OpenAlex institution history
    uni = st.normalize_name(_uni_core(inst.get("name", "")))
    if not any(uni in st.normalize_name(i) for i in c["institutions"]):
        return dict(base, match_note=f"The AI picked OpenAlex author {c['id']}, but its institutions ({', '.join(c['institutions'][:3]) or 'none'}) "
                                     f"do not include {_uni_core(inst.get('name', ''))}; not accepted."), "NO_RESULT_FOUND"
    return dict(base, openalex_author_id=c["id"], match_status="MATCHED", match_method="LLM_VERIFIED",
                match_note=f"The AI ({meta['model_used']}) chose OpenAlex author {c['id']} from {len(cands)} candidates "
                           f"(confidence {ans['confidence']:.2f}: {ans['reason']}); {_uni_core(inst.get('name', ''))} confirmed in its affiliations."), ""


def _llm_publications(p, inst):
    """Last resort. Local model chooses among real OpenAlex candidates; Python confirms
    name and university. Only use the cloud publication task when no local model is configured."""
    from services import llm_local
    ai = _ai()
    if llm_local.configured():
        try:
            return _local_llm_pick(p, inst)
        except fx.RateLimited:
            raise
        except llm_local.LocalLLMError as e:
            failed = str(e)
            print(f"[pipeline] local model failed for {p['name']}: {failed}")
            return {"llm_papers_checked": st.now_iso(),
                    "llm_papers_ai": {"model_used": os.environ.get("LLM_MODEL", ""), "last_error": failed[:300]}}, "LLM_FAILED"
    ai = _ai()
    if not ai:
        return {}, ""
    page_text = ""
    if p.get("faculty_url"):
        pg = fx.fetch_page(p["faculty_url"])
        if pg.get("ok") and st.normalize_name(p["name"]).split()[-1] in st.normalize_name(pg["text"]):
            page_text = pg["text"]                      # only a page that actually mentions the person
    papers, meta = None, {}
    if True:
        o = ai.find_publications(p["name"], inst.get("name", ""), p.get("department", ""), p.get("title", ""), page_text)
        meta = _ai_meta(o)
        if o.status != "STAFF_REVIEW":
            papers = list(o.value or [])
        else:
            failed = (failed + " | " if failed else "") + o.last_error[:200]
    base = {"llm_papers_checked": st.now_iso(), "llm_papers_ai": dict(meta, last_error=failed[:300]) if failed else meta}
    if papers is None:
        return base, "LLM_FAILED"
    if not papers:
        return base, "NO_RESULT_FOUND"                  # the model knows of no papers: an accepted answer
    aid, verified = _llm_papers_to_author(p, inst, papers)
    if aid:
        return dict(base, openalex_author_id=aid, match_status="MATCHED", match_method="LLM_VERIFIED",
                    match_note=f"Publications suggested by the AI ({meta.get('model_used', '')}) were verified in OpenAlex: "
                               f"{len(verified)} work(s) with this author, e.g. \"{verified[0][:90]}\"."), ""
    return dict(base, match_note="The AI suggested publications, but none could be verified in OpenAlex for this person."), "NO_RESULT_FOUND"


IDENTITY_REASONS = dict(SCHOLAR_REASONS, **{
    "NO_RESULT_FOUND": "No Google Scholar profile, no ORCID-confirmed OpenAlex author, and no publications the AI could name and verify. The professor may have no indexed papers.",
    "LLM_FAILED": "Every AI model failed while looking for publications; nothing could be checked.",
})


IDENTITY_STEPS = ("openalex_ai", "scholar", "orcid", "pages")


def identity_ladder(p, inst, inst_oid, done=None):
    """One identity ladder, used by the pipeline AND by Re-queue (OpenAlex name + institution has
    already failed when this runs). Steps, in order; finished steps are passed in `done` and skipped:
      openalex_ai - local AI picks among real OpenAlex candidates (name, department, university);
                    Python then checks the pick's institutions include this university
      scholar     - Google Scholar profile, verified by name + this university (affiliation or
                    verified email); its papers -> OpenAlex author, else the Scholar papers themselves
      orcid       - ORCID record with name + this university -> its DOIs -> OpenAlex author
      pages       - faculty page / personal / lab page (and a web search for the professor's own
                    site): publications listed there
    Returns (fields, done, status). status: MATCHED / SCHOLAR / FACULTY_PAGE / NO_RESULT_FOUND /
    RETRY_LATER (a step could not run now - cooldown or model offline - nothing decided)."""
    from services import scholar, orcid
    done = list(done or [])
    notes = []

    def finish(fields, status):
        return dict(fields, identity_checked=st.now_iso(), identity_steps=done), done, status

    if "openalex_ai" not in done:
        fields, reason = _llm_publications(p, inst)
        if reason == "LLM_FAILED":
            return {}, done, "RETRY_LATER"
        done.append("openalex_ai")
        if fields.get("match_status") == "MATCHED":
            return finish(fields, "MATCHED")
        if fields.get("match_note"):
            notes.append(fields["match_note"])
    if "scholar" not in done:
        try:
            got, why = scholar.match_via_scholar(p, inst, inst_oid, names_match)
        except fx.RateLimited:
            raise
        except Exception as e:
            print(f"[pipeline] scholar step failed for {p['name']}: {e}")
            got, why = {}, ""
        if not got and not why:
            return {}, done, "RETRY_LATER"           # search cooling down: resume at this step
        done.append("scholar")
        if got.get("match_status") == "MATCHED":
            return finish(got, "MATCHED")
        if got.get("match_status") == "SCHOLAR":
            return finish(_save_scholar_works(p, got), "SCHOLAR")
        notes.append("No verified Google Scholar profile.")
    if "orcid" not in done:
        try:
            got, why = orcid.match_via_orcid(p, inst, names_match, lambda a, b: scholar._names_ok(names_match, a, b))
        except fx.RateLimited:
            raise
        except Exception as e:
            print(f"[pipeline] orcid step failed for {p['name']}: {e}")
            got, why = {}, ""
        if not got and not why:
            return {}, done, "RETRY_LATER"
        done.append("orcid")
        if got.get("match_status") == "MATCHED":
            return finish(got, "MATCHED")
        notes.append("No ORCID record confirmed in OpenAlex." if why else "")
    if "pages" not in done:
        fp = use_faculty_page(p, inst)
        if not fp:
            found = _identity_from_search(p, inst)
            if found is None:
                return {}, done, "RETRY_LATER"
            if found:
                _save_page_works(p, inst, found["url"], found["pubs"], found["grants"], "the professor's own website found by web search")
                fp = {"match_status": "FACULTY_PAGE", "match_method": "FACULTY_PAGE",
                      "match_note": f"Publications taken from the professor's own website ({found['url']})."}
        done.append("pages")
        if fp:
            return finish(fp, "FACULTY_PAGE")
    return finish({"match_status": "NO_RESULT_FOUND",
                   "match_note": "Checked: OpenAlex candidates with the AI, Google Scholar, ORCID, the faculty page and "
                                 "pages it links to, and a web search. " + " ".join(n for n in notes if n)}, "NO_RESULT_FOUND")


def _save_scholar_works(p, got):
    """A verified Scholar profile without an OpenAlex match: its publications become the record."""
    from services import facultypage as fpg
    ids = []
    for x in got.pop("scholar_papers", []):
        pid = fpg.item_id("GS", p["id"], x["title"])
        st.upsert_paper({"openalex_work_id": pid, "doi": "", "title": x["title"], "publication_year": x["year"],
                         "publication_date": f"{x['year']}-01-01" if x["year"] else "", "source_name": x["venue"][:200],
                         "paper_url": got.get("scholar_url", ""), "citation_count": 0, "subfield": "", "field": "",
                         "source": "GOOGLE_SCHOLAR", "authors": x["authors"][:300]})
        ids.append(pid)
    return dict(got, paper_ids=ids, pipeline_done=True, last_openalex_update=st.now_iso())


def resolve_unmatched(p, inst, inst_oid):
    """Pipeline entry: OpenAlex name + institution failed -> run the identity ladder. Grants and
    hiring run afterwards for every outcome (process_professor), so a professor with no indexed
    papers still gets faculty-page grants and hiring signals. Unresolved -> Staff review."""
    fields, done, status = identity_ladder(p, inst, inst_oid)
    if status == "RETRY_LATER":
        return {}                                    # resumes from the same step on the next pass
    if status in ("MATCHED", "SCHOLAR", "FACULTY_PAGE"):
        st.db().staff_review.delete_many({"task_type": "OPENALEX_IDENTITY", "professor_id": p["id"]})
        return fields
    flag_staff_review("OPENALEX_IDENTITY", "NO_RESULT_FOUND", inst, p, source_url=p.get("faculty_url", ""),
                      extra={"last_error": fields.get("match_note", ""), "steps_done": done,
                             "openalex_note": p.get("match_note", ""), "department": p.get("department", "")})
    return fields


GRANT_YEARS = 10


def grant_canon_key(funder, award_id):
    """funder + award number, normalized: "NSF", "1919631" -> "nsf:1919631". "" when no award number."""
    digits = re.sub(r"\D", "", str(award_id or ""))
    if len(digits) < 5:
        return ""
    f = re.sub(r"[^a-z]", "", (funder or "").lower())
    f = "nsf" if "nationalsciencefoundation" in f or f.startswith("nsf") else ("nih" if "nationalinstitutesofhealth" in f or f.startswith("nih") else f[:20])
    return f"{f}:{digits[-7:]}"


def _store_grant(g, source_url=""):
    """Upsert one grant (from NSF / NIH / faculty page) and return its link for the professor.
    The same funder award already stored (e.g. from OpenAlex) is reused instead of duplicated."""
    gid = g["key"]
    canon = grant_canon_key(g.get("funder_name"), g.get("funder_award_id"))
    if canon:
        existing = st.db().grants.find_one({"canon_key": canon}, {"_id": 1})
        if existing:
            return {"id": existing["_id"], "role": g.get("role", "RECIPIENT")}
    start = g.get("start_date") or (f"{g['year']}-01-01" if g.get("year") else "")
    st.upsert_grant({"openalex_award_id": gid, "title": g.get("title", ""), "funder_name": g.get("funder_name", ""),
                     "funder_award_id": g.get("funder_award_id", ""), "amount": float(g.get("amount") or 0),
                     "currency": g.get("currency", ""), "start_date": start, "end_date": g.get("end_date", ""),
                     "landing_page_url": g.get("url") or source_url, "evidence": g.get("evidence", ""),
                     "source": g.get("source", ""), "year_found": g.get("year", 0)})
    return {"id": gid, "role": g.get("role", "RECIPIENT")}


def collect_grants(p, inst):
    """Every grant source, merged, last GRANT_YEARS years:
      1. OpenAlex awards on the professor's works (matched professors only)
      2. NSF + NIH award search by name, filtered to this university
      3. fellowships/grants named on the official faculty page
    Returns (links, counts per source). Duplicates (same funder award id) are kept once."""
    from services import federal_grants, facultypage
    links, seen, counts = [], set(), {"openalex": 0, "nsf_nih": 0, "faculty_page": 0}
    since = str(time.gmtime().tm_year - GRANT_YEARS)

    def key(award_id, title):
        # same award from two sources: digits of the award number, else the normalized title
        digits = re.sub(r"\D", "", str(award_id or ""))
        return digits[-7:] if len(digits) >= 6 else re.sub(r"[^a-z0-9]", "", (title or "").lower())[:60]

    if p.get("openalex_author_id"):
        resolve_institution(inst)
        _ids, _s, _f, awards = ingest_works(p)
        for link in discover_grants(dict(p), inst, awards):
            g = st.db().grants.find_one({"_id": link["id"]}) or {}
            if (g.get("start_date") or "9999")[:4] >= since:
                links.append(link)
                seen.add(key(g.get("funder_award_id"), g.get("title")))
                seen.add(key("", g.get("title")))
                counts["openalex"] += 1
    for g in federal_grants.search(p["name"], inst.get("name", ""), names_match, GRANT_YEARS):
        k1, k2 = key(g["funder_award_id"], g["title"]), key("", g["title"])
        if k1 in seen or k2 in seen:
            continue
        seen.update([k1, k2])
        links.append(_store_grant(g))
        counts["nsf_nih"] += 1
    if p.get("faculty_url"):
        page = fx.fetch_page(p["faculty_url"])
        if page.get("ok"):
            for g in facultypage.grants(page["text"]):
                if g.get("year") and str(g["year"]) < since:
                    continue
                gid = facultypage.item_id("FG", p["id"], g["title"])
                if gid in [l["id"] for l in links]:
                    continue
                links.append(_store_grant(dict(g, key=gid, role="RECIPIENT", source="FACULTY_PAGE"), p["faculty_url"]))
                counts["faculty_page"] += 1
    return links, counts


def run_grant_job(professor_id):
    """One grant check for one MATCHED professor: their recent works' OpenAlex awards, kept only
    where the professor is a named person on the award or their institution is the awardee."""
    p = st.get_professor(professor_id)
    inst = st.get_institution(p["institution_id"]) if p else None
    if not p or not inst:
        return {"ok": False, "message": "Professor not found."}
    grants, counts = collect_grants(p, inst)
    st.update_professor(p["id"], {"grants": grants, "grant_count": len(grants), "last_grant_update": st.now_iso()})
    st.recount(inst["id"])
    st.invalidate_search()
    return {"ok": True, "professor": p["name"], "university": inst["name"], "by_source": counts,
            "grants_linked": len(grants), "grants": [st._clean(g) for g in st.db().grants.find({"_id": {"$in": [g["id"] for g in grants]}})]}


def run_llm_identity_job(professor_id):
    """Only the AI step of the identity chain, for one professor (to test the model end to end).
    A match is stored; an empty or unverifiable answer goes to Staff review as NO_RESULT_FOUND."""
    p = st.get_professor(professor_id)
    inst = st.get_institution(p["institution_id"]) if p else None
    if not p or not inst:
        return {"ok": False, "message": "Professor not found."}
    ai = _ai()
    if not ai:
        return {"ok": False, "message": "No AI model configured."}
    fields, reason = _llm_publications(p, inst)
    meta = fields.get("llm_papers_ai") or {}
    if fields.get("match_status") == "MATCHED":
        st.db().staff_review.delete_many({"task_type": "OPENALEX_IDENTITY", "professor_id": p["id"]})
        fields.update(pipeline_done=False, identity_checked=st.now_iso())
    elif reason:
        flag_staff_review("OPENALEX_IDENTITY", reason, inst, p, source_url=p.get("faculty_url", ""),
                          extra={"last_error": IDENTITY_REASONS.get(reason, reason), "department": p.get("department", "")})
        if reason == "NO_RESULT_FOUND":
            fields.update(match_status="NO_RESULT_FOUND", identity_checked=st.now_iso())
    st.update_professor(p["id"], fields)
    st.invalidate_search()
    return {"ok": True, "professor": p["name"], "department": p.get("department", ""), "university": inst["name"],
            "models": ai.chain(), "model_used": meta.get("model_used", ""), "result": fields.get("match_status") or reason,
            "openalex_author_id": fields.get("openalex_author_id", ""), "note": fields.get("match_note", "")}


def use_faculty_page(p, inst):
    """No OpenAlex / Scholar / ORCID identity: take publications and grants from the professor's
    own official faculty page. Returns the professor fields to store, or {} if the page lists none."""
    from services import facultypage as fpg
    url = p.get("faculty_url") or ""
    if not url:
        return {}
    pubs, grs, ok, used = fpg.from_page(url, p.get("name", ""))
    if not ok or not (pubs or grs):
        return {}
    url = used[-1] if used else url
    ids, grant_links = [], []
    for x in pubs:
        pid = fpg.item_id("FP", p["id"], x["title"])
        st.upsert_paper({"openalex_work_id": pid, "doi": "", "title": x["title"], "publication_year": x["year"],
                         "publication_date": f"{x['year']}-01-01" if x["year"] else "", "source_name": x["venue_text"][:200],
                         "paper_url": x["url"] or url, "citation_count": 0, "subfield": "", "field": "",
                         "source": "FACULTY_PAGE", "faculty_page_url": url})
        ids.append(pid)
    for g in grs:
        gid = fpg.item_id("FG", p["id"], g["title"])
        grant_links.append(_store_grant(dict(g, key=gid, role="RECIPIENT", source="FACULTY_PAGE"), url))
    # federal awards by name at this university (NSF, NIH), same as matched professors
    from services import federal_grants
    for g in federal_grants.search(p["name"], inst.get("name", ""), names_match, GRANT_YEARS):
        grant_links.append(_store_grant(g))
    return {"paper_ids": ids, "grants": grant_links, "grant_count": len(grant_links), "match_status": "FACULTY_PAGE",
            "match_method": "FACULTY_PAGE",
            "match_note": f"No OpenAlex, Google Scholar or ORCID identity. {len(ids)} publications and {len(grant_links)} grants/fellowships "
                          f"taken from {'the official faculty page' if len(used) == 1 else 'the page linked from the faculty page'} ({url}).",
            "last_openalex_update": st.now_iso(), "last_grant_update": st.now_iso(), "identity_checked": st.now_iso()}


def _identity_from_search(p, inst):
    """Web search (DDGS) for the professor's own pages beyond the faculty page (personal site,
    lab site), then read publications from the first page that lists them."""
    from services import websearch, facultypage as fpg, discovery
    q = f'"{p["name"]}" {re.split(r"[-,]", inst.get("name", ""))[0]} {p.get("department", "")} publications'
    try:
        rows = websearch.search(q, 8)
    except websearch.SearchUnavailable:
        return None                                     # cooling down: try later
    last = st.normalize_name(p["name"]).split()[-1]
    for r in rows:
        url, host = r["url"], urllib.parse.urlparse(r["url"]).netloc.lower()
        if any(b in host for b in fpg.SKIP_HOSTS) or url == p.get("faculty_url") or last not in st.normalize_name(r.get("title", "") + " " + url):
            continue
        page = fx.fetch_page(url)
        if not page.get("ok") or last not in st.normalize_name(page["text"][:3000]):
            continue                                    # the page must be about this person
        pubs = fpg.publications(page["text"])
        if pubs:
            return {"url": url, "pubs": pubs, "grants": fpg.grants(page["text"])}
    return {}


def _save_page_works(p, inst, url, pubs, grs, how):
    from services import facultypage as fpg
    ids = []
    for x in pubs:
        pid = fpg.item_id("FP", p["id"], x["title"])
        st.upsert_paper({"openalex_work_id": pid, "doi": "", "title": x["title"], "publication_year": x["year"],
                         "publication_date": f"{x['year']}-01-01" if x["year"] else "", "source_name": x["venue_text"][:200],
                         "paper_url": x["url"] or url, "citation_count": 0, "subfield": "", "field": "",
                         "source": "FACULTY_PAGE", "faculty_page_url": url})
        ids.append(pid)
    links = [_store_grant(dict(g, key=fpg.item_id("FG", p["id"], g["title"]), role="RECIPIENT", source="FACULTY_PAGE"), url) for g in grs]
    st.update_professor(p["id"], {"paper_ids": ids, "grants": links, "grant_count": len(links), "match_status": "FACULTY_PAGE",
                                  "match_method": "FACULTY_PAGE", "identity_checked": st.now_iso(), "pipeline_done": True,
                                  "match_note": f"No OpenAlex, Google Scholar or ORCID identity. {len(ids)} publications and "
                                                f"{len(links)} grants/fellowships taken from {how} ({url}).",
                                  "last_openalex_update": st.now_iso(), "last_grant_update": st.now_iso()})


def _identity_next_steps(item, p, inst):
    """Unresolved OpenAlex identity: run only the steps not yet done for this item, in order
       llm_pick  - local model chooses among real OpenAlex candidates (name, department, university)
       pages     - publications on the faculty page, or a personal / lab page it links to
       search    - DDGS web search for the professor's own site with a publication list
    Steps already finished are recorded on the item ("steps_done") and never repeated."""
    d = st.db()
    # old items recorded the previous step names; map them so finished work is not repeated
    legacy = {"llm_pick": "openalex_ai", "search": "pages"}
    done = list(dict.fromkeys(legacy.get(s, s) for s in (item.get("steps_done") or []) if legacy.get(s, s) in IDENTITY_STEPS))
    if item.get("reason") == "NO_RESULT_FOUND" and set(done) >= {"openalex_ai", "pages"}:
        done = [s for s in done if s != "pages"]    # Scholar + ORCID were added in between: re-check pages after them
    fields, done, status = identity_ladder(p, inst, resolve_institution(inst), done)
    if status == "RETRY_LATER":
        return _keep(item, done, "A step could not run right now (web search cooldown or AI model offline); "
                                 "re-queue continues from step: " + next(s for s in IDENTITY_STEPS if s not in done) + ".")
    finish_professor(p, inst, fields)
    if status != "NO_RESULT_FOUND":
        d.staff_review.delete_one({"_id": item["_id"]})
        return "FIXED_" + status
    d.staff_review.update_one({"_id": item["_id"]}, {"$set": {
        "steps_done": done, "reason": "NO_RESULT_FOUND", "attempted_at": st.now_iso(),
        "last_error": fields.get("match_note", "")}}, upsert=True)
    return "NO_RESULT_FOUND"


def finish_professor(p, inst, fields):
    """After an identity is settled (any outcome): store it, then papers + subfields (OpenAlex),
    grants (OpenAlex, NSF/NIH, faculty page) and hiring (faculty / lab / personal page)."""
    st.update_professor(p["id"], fields)
    q = dict(p, **fields)
    if q.get("match_status") == "MATCHED" and q.get("openalex_author_id"):
        ids, subs, flds, _aw = ingest_works(q)
        st.update_professor(p["id"], {"paper_ids": ids, "subfields": subs, "fields": flds, "last_openalex_update": st.now_iso()})
    try:
        links, _c = collect_grants(st.get_professor(p["id"]), inst)
        st.update_professor(p["id"], {"grants": links, "grant_count": len(links), "last_grant_update": st.now_iso()})
    except fx.RateLimited:
        raise
    except Exception as e:
        flag_staff_review("GRANT_SEARCH", "GRANT_SEARCH_FAILED", inst, p, source_url=p.get("faculty_url", ""),
                          extra={"last_error": f"{type(e).__name__}: {str(e)[:200]}", "department": p.get("department", "")})
    h = check_hiring(st.get_professor(p["id"]), inst)
    if h is not None and h["status"] != "STAFF_REVIEW":
        st.update_professor(p["id"], {"hiring": h, "has_hiring": h["status"] in HIRING_POSITIVE,
                                      "hiring_status": h["status"], "last_hiring_update": h["checked_at"]})
    st.update_professor(p["id"], {"pipeline_done": True})
    st.invalidate_search()


def _grant_next_steps(item, p, inst):
    """Grant search stuck: faculty page first, then NSF / NIH by name, then OpenAlex awards."""
    links, counts = collect_grants(p, inst)
    st.update_professor(p["id"], {"grants": links, "grant_count": len(links), "last_grant_update": st.now_iso()})
    st.db().staff_review.delete_one({"_id": item["_id"]})
    return f"FIXED_GRANTS_{len(links)}"


def _keep(item, done, why):
    """Nothing decided now: keep the item with the steps already finished, retried later."""
    st.db().staff_review.update_one({"_id": item["_id"]}, {"$set": {"steps_done": done, "last_error": why,
                                                                     "attempted_at": st.now_iso(), "resolved": False}}, upsert=True)
    return "RETRY_LATER"


def retry_review_item(item_id):
    """Re-run the task behind one open staff review item. Clears it on success; a new failure
    re-flags it with the CURRENT error, so the list only shows real, present-day problems."""
    d = st.db()
    item = d.staff_review.find_one({"_id": item_id})
    if not item:
        return {"ok": False, "message": "Review item not found."}
    t, pid = item.get("task_type"), item.get("professor_id")
    p = st.get_professor(pid) if pid else None
    inst = st.get_institution(item.get("institution_id") or (p or {}).get("institution_id", ""))
    if inst is None:
        return {"ok": False, "message": "University not found."}
    res = "STILL_FAILING"
    if t == "PROFILE_EXTRACTION" and p:
        d.staff_review.delete_one({"_id": item_id})
        fields = enrich_profile(dict(p, profile_extracted=False), inst)
        if fields.get("profile_extracted"):
            st.update_professor(p["id"], fields)
            res = "FIXED"
        elif not fields and not d.staff_review.find_one({"task_type": t, "professor_id": pid, "resolved": False}):
            # faculty page did not load (bot check / timeout): try again later, at most 3 times,
            # then say so plainly instead of looping
            tries = int(item.get("page_tries") or 0) + 1
            reason = "FACULTY_PAGE_UNREACHABLE" if tries >= 3 else item.get("reason")
            d.staff_review.replace_one({"_id": item_id}, dict(item, page_tries=tries, reason=reason, attempted_at=st.now_iso(),
                                       last_error=("The faculty page could not be loaded after 3 tries (the site may block "
                                                   "automated requests). Check the link or add the details by hand.")
                                       if tries >= 3 else item.get("last_error")), upsert=True)
            res = "PAGE_UNREACHABLE" if tries >= 3 else "RETRY_LATER"
        # else enrich_profile re-flagged it with the current error -> STILL_FAILING
    elif t == "HIRING_EXTRACTION" and p:
        d.staff_review.delete_one({"_id": item_id})
        h = check_hiring(p, inst)
        if h is None:
            d.staff_review.replace_one({"_id": item_id}, item, upsert=True)
            res = "RETRY_LATER"
        elif h["status"] != "STAFF_REVIEW":
            st.update_professor(p["id"], {"hiring": h, "has_hiring": h["status"] in HIRING_POSITIVE,
                                          "hiring_status": h["status"], "last_hiring_update": h["checked_at"]})
            res = "FIXED"
    elif t == "OPENALEX_IDENTITY" and p:
        res = _identity_next_steps(item, p, inst)
    elif t == "GRANT_SEARCH" and p:
        res = _grant_next_steps(item, p, inst)
    else:
        return {"ok": False, "message": f"No automatic retry for task type {t}."}
    st.invalidate_search()
    return {"ok": True, "item": item_id, "task_type": t, "professor": (p or {}).get("name", ""), "result": res}


def queue_review_retries(task_type=""):
    """Re-queue (staff button): one job per open review item, at the TOP of the queue (priority 1).
    An item that already has a job waiting or running is not queued twice. An item whose every step
    already ran (or whose page needs a person) gets one fresh full pass - sites, Scholar and OpenAlex
    change over time - and the steps record is reset only for that pass.
    Returns (queued, skipped_already_waiting)."""
    d = st.db()
    # jobs left RUNNING by a restart are stale: put them back in the queue
    d.jobs.update_many({"kind": "REVIEW_RETRY", "status": "RUNNING"}, {"$set": {"status": "QUEUED", "queued_at": st.now_iso()}})
    q = {"resolved": False}
    if task_type:
        q["task_type"] = task_type
    n, waiting = 0, 0
    for item in d.staff_review.find(q, {"_id": 1, "reason": 1, "steps_done": 1}):
        jid = f"REVIEW_RETRY:{item['_id']}"
        job = d.jobs.find_one({"_id": jid}, {"status": 1})
        if job and job.get("status") in ("QUEUED", "RUNNING"):
            waiting += 1
            continue
        exhausted = (item.get("reason") == "NO_RESULT_FOUND" and set(IDENTITY_STEPS) <= set(item.get("steps_done") or [])) \
            or item.get("reason") == "FACULTY_PAGE_UNREACHABLE"
        if exhausted:
            d.staff_review.update_one({"_id": item["_id"]}, {"$set": {"steps_done": [], "page_tries": 0}})
        d.staff_review.update_one({"_id": item["_id"]}, {"$set": {"queue_status": "QUEUED", "queued_at": st.now_iso()}})
        queue_test_job("REVIEW_RETRY", item["_id"], priority=1)
        n += 1
    return n, waiting


def recent_jobs(limit=10):
    """Queue view for staff: running first, then waiting (re-queued review jobs at the top, in run
    order), then the most recently finished."""
    d = st.db()
    order = []
    order += list(d.jobs.find({"status": "RUNNING"}).sort("started_at", -1))
    order += list(d.jobs.find({"status": "QUEUED"}).sort([("priority", -1), ("queued_at", 1)]))
    order += list(d.jobs.find({"status": {"$in": ["DONE", "FAILED"]}}).sort("finished_at", -1).limit(limit))
    out = []
    for j in order[:limit]:
        res = j.get("result") or {}
        target = str(j.get("professor_id") or "")
        item = d.staff_review.find_one({"_id": target}) if j.get("kind") == "REVIEW_RETRY" else None
        prof = (item or {}).get("professor") or res.get("professor") or ""
        if not prof and target:
            pp = st.get_professor(target.split(":http")[0]) if ":" in target else None
            prof = (pp or {}).get("name", "")
        out.append({"id": str(j["_id"]), "kind": j.get("kind", ""), "status": j.get("status", ""),
                    "professor": prof, "task": (item or {}).get("task_type") or target.split(":")[0],
                    "result": str(res.get("result") or res.get("message") or res.get("grants_linked") or ""),
                    "when": j.get("finished_at") or j.get("started_at") or j.get("queued_at") or ""})
    return out


def review_queue_state():
    d = st.db()
    return {"queued": d.jobs.count_documents({"kind": "REVIEW_RETRY", "status": "QUEUED"}),
            "running": d.jobs.count_documents({"kind": "REVIEW_RETRY", "status": "RUNNING"})}


def run_hiring_job(professor_id):
    """Re-run the hiring check (own pages, then web search) for one professor with the current
    strict rules. Keeps the previous result when the check could not run (model or search down)."""
    p = st.get_professor(professor_id)
    if not p:
        return {"ok": False, "message": "Professor not found."}
    inst = st.get_institution(p.get("institution_id", ""))
    if inst is None:
        return {"ok": False, "message": "University not found."}
    h = check_hiring(p, inst)
    if h is None:
        return {"ok": True, "professor": p["name"], "result": "RETRY_LATER"}
    if h["status"] == "STAFF_REVIEW":
        st.update_professor(p["id"], {"last_hiring_update": h["checked_at"]})
        return {"ok": False, "professor": p["name"], "result": "STAFF_REVIEW"}
    st.update_professor(p["id"], {"hiring": h, "has_hiring": h["status"] in HIRING_POSITIVE,
                                  "hiring_status": h["status"], "last_hiring_update": h["checked_at"]})
    st.invalidate_search()
    return {"ok": True, "professor": p["name"], "result": h["status"],
            "message": f"{p['name']}: {h['status']}" + (f" | {h['quote'][:100]}" if h.get("quote") else "")}


def queue_hiring_checks(query=None, priority=1):
    """Queue one HIRING_CHECK job per professor matching query (default: every processed professor)."""
    n = 0
    for p in st.db().professors.find(query if query is not None else {"pipeline_done": True}, {"_id": 1}):
        queue_test_job("HIRING_CHECK", p["_id"], priority=priority)
        n += 1
    return n


def queue_test_job(kind, professor_id, priority=0):
    """Store a one-off job; the background worker runs it before anything else (higher priority first)."""
    jid = f"{kind}:{professor_id}"
    st.db().jobs.replace_one({"_id": jid}, {"_id": jid, "kind": kind, "professor_id": professor_id, "priority": priority,
                                           "status": "QUEUED", "queued_at": st.now_iso()}, upsert=True)
    return jid


def run_next_job():
    """Runs one queued one-off job (grant check / LLM identity). Returns a log message or ""."""
    # staff re-queues (priority 1) run before anything else, then oldest first. Hiring checks need
    # web search: while it is paused (daily cap / cooldown) they wait instead of re-reading the same
    # pages with the AI and failing at the search step every few seconds.
    from services import websearch as _ws
    q = {"status": "QUEUED"}
    if not _ws.status()["available"]:
        q["kind"] = {"$ne": "HIRING_CHECK"}
    job = st.db().jobs.find_one(q, sort=[("priority", -1), ("queued_at", 1)])
    if job is None:
        return ""
    st.db().jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "RUNNING", "started_at": st.now_iso()}})
    if job["kind"] == "REVIEW_RETRY":
        st.db().staff_review.update_one({"_id": job["professor_id"]}, {"$set": {"queue_status": "RUNNING"}})
    try:
        if job["kind"] == "GRANT_CHECK":
            res = run_grant_job(job["professor_id"])
        elif job["kind"] == "REVIEW_RETRY":
            res = retry_review_item(job["professor_id"])
        elif job["kind"] == "HIRING_CHECK":
            res = run_hiring_job(job["professor_id"])
        else:
            res = run_llm_identity_job(job["professor_id"])
        status = "DONE" if res.get("ok") else "FAILED"
        if res.get("result") == "RETRY_LATER":
            status = "QUEUED"                  # could not run right now (search cooldown / reload): try again
            st.db().jobs.update_one({"_id": job["_id"]}, {"$set": {"queued_at": st.now_iso()}})
    except fx.RateLimited:
        st.db().jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "QUEUED"}})
        raise
    except ImportError as e:
        # a package went missing (sandbox reset before `jac install` ran): an environment problem,
        # not a result - keep the job queued so it runs once the package is back
        log(f"Job {job['_id']} postponed: {e}. Run `jac install` to restore dependencies.")
        st.db().jobs.update_one({"_id": job["_id"]}, {"$set": {"status": "QUEUED", "queued_at": st.now_iso()}})
        time.sleep(30)
        return f"Job {job['_id']}: QUEUED - missing package ({e.name})"
    except Exception as e:
        res, status = {"ok": False, "message": f"{type(e).__name__}: {str(e)[:300]}"}, "FAILED"
    st.db().jobs.update_one({"_id": job["_id"]}, {"$set": {"status": status, "result": res, "finished_at": st.now_iso()}})
    if job["kind"] == "REVIEW_RETRY":
        # fixed items are already deleted from the review list; anything left shows its outcome
        st.db().staff_review.update_one({"_id": job["professor_id"]}, {"$set": {
            "queue_status": "WAITING" if status == "QUEUED" else "RETRIED",
            "last_retry_result": str(res.get("result") or res.get("message") or ""), "last_retry_at": st.now_iso()}})
    return f"Job {job['_id']}: {status} - {res.get('result') or res.get('grants_linked', res.get('message', ''))}"


def scholar_backfill_one():
    """Professors left UNRESOLVED before the Google Scholar step existed: give each one Scholar
    check when the worker is otherwise idle. Returns a log message, or "" when nothing to do."""
    from services import websearch as ws
    if not ws.status()["available"]:
        return ""
    # every professor still UNRESOLVED that has not been through the full Scholar -> ORCID -> AI chain
    doc = st.db().professors.find_one({"match_status": "UNRESOLVED", "identity_checked": {"$exists": False}})
    if doc is None:
        return ""
    p = st._clean(doc)
    inst = st.get_institution(p["institution_id"])
    if inst is None:
        st.update_professor(p["id"], {"scholar_checked": st.now_iso()})
        return ""
    out = resolve_unmatched(p, inst, resolve_institution(inst))
    if not out:
        return ""                              # a step cooled down mid-check; retried later
    finish_professor(p, inst, out)             # papers, grants and hiring for every outcome
    return f"{p['name']} ({inst['name']}): identity ladder -> {out.get('match_status')}"


_NOT_A_PAPER = re.compile(
    r"^(data for |dataset|supplementa|supporting information|additional file|figure s?\d|table s?\d|"
    r"video s?\d|replication (data|package|files)|source data|peer review file|reporting summary)", re.I)


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def clean_works(works, limit=50):
    """Scholarly works only, each publication once: drops dataset / supplementary / figure objects
    (by title, in case OpenAlex typed them as articles), then de-duplicates by DOI, then by
    normalized title + year. Keeps the most-cited copy of a duplicate."""
    keep, by_doi, by_title = [], {}, {}
    for w in works:
        title = w.get("title") or w.get("display_name") or ""
        if not title.strip() or _NOT_A_PAPER.search(title.strip()):
            continue
        doi = fx.clean_doi(w.get("doi") or "") if w.get("doi") else ""
        tkey = (_norm_title(title), int(w.get("publication_year") or 0))
        prev = by_doi.get(doi) if doi else None
        if prev is None:
            prev = by_title.get(tkey)
        if prev is not None:
            if int(w.get("cited_by_count") or 0) > int(prev.get("cited_by_count") or 0):
                keep[keep.index(prev)] = w
                if doi:
                    by_doi[doi] = w
                by_title[tkey] = w
            continue
        keep.append(w)
        if doi:
            by_doi[doi] = w
        by_title[tkey] = w
    return keep[:limit]


def ingest_works(p):
    """Recent papers (last 5 years) + their OpenAlex subfields. Returns (paper_ids, subfields, fields, award_ids)."""
    year = time.gmtime().tm_year - 4
    ids, subs, flds, awards = [], [], [], []
    for w in clean_works(fx.fetch_author_works(p["openalex_author_id"], year)):
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
    # OpenAlex returns institution_awarded as one object or as a list of them
    ia = award.get("institution_awarded") or []
    awarded_ids = [_sid(x.get("id")) for x in (ia if isinstance(ia, list) else [ia]) if isinstance(x, dict)]
    awarded = inst.get("openalex_institution_id") if inst.get("openalex_institution_id") in awarded_ids else (awarded_ids[0] if awarded_ids else "")
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
        "local_model_error": getattr(outcome, "local_model_error", "") if outcome else "",
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
    if o.status == "RETRY_LATER":
        return {}                     # code reload hiccup, not a model failure; retried on the next run
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


import re as _re

# Announcements, not openings: "we are delighted to welcome X as our postdoc", "X joined the lab".
_NOT_HIRING = _re.compile(
    r"\b(welcom(e|es|ed|ing)|congratulat\w*|joined|has joined|have joined|is joining|will join us as|"
    r"was (hired|appointed|named|awarded)|has been (hired|appointed|named|awarded)|"
    r"appointed|promoted|award(ed)? (a|the)|receiv(ed|es) (a|the)|alumni|former (student|postdoc)|"
    r"graduated|defended)\b", _re.I)
# Words that make a sentence an open invitation.
_OPEN_CALL = _re.compile(
    r"\b(recruit\w*|hiring|openings?|open positions?|vacanc\w*|seeking|looking for|accepting|"
    r"apply|applications?|applicants?|prospective|interested (students|candidates|applicants)|"
    r"positions? (are |is )?available|join (my|our|the|his|her|their) (lab|group|team)|please contact|"
    r"contact (me|prof|dr)|interest(ed)? in (working|joining)|available for (new )?(graduate|phd|students|advisees))\b", _re.I)


_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december|spring|summer|fall|autumn|winter"


def hiring_expired(quote):
    """True when the sentence dates itself in the past: "openings beginning in Fall 2023",
    "will start September 2020", "join us in spring, summer 2026" (checked in October 2026).
    The latest year mentioned must not be before this year; if it is this year, a named season /
    month must not already be over."""
    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", quote or "")]
    if not years:
        return False
    now = time.gmtime()
    latest = max(years)
    if latest < now.tm_year:
        return True
    if latest > now.tm_year:
        return False
    order = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
             "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
             "winter": 2, "spring": 5, "summer": 8, "fall": 11, "autumn": 11}
    named = [order[m] for m in re.findall(rf"\b({_MONTHS})\b", (quote or "").lower())]
    return bool(named) and max(named) < now.tm_mon


def hiring_quote_complete(quote):
    """A cut-off sentence ("We are looking for a highly motivated postdoctoral candidates who")
    is an extraction error, not evidence."""
    q = (quote or "").strip().rstrip("_*").strip()
    if len(q) < 25:
        return False
    if q[-1] in ".!?)\"'":
        return True
    return not re.search(r"\b(who|which|that|and|or|to|for|with|in|of|the|a|an|our|is|are)$", q, re.I)


def hiring_sentence_ok(quote):
    """Plain-code gate before any AI verdict is trusted: an open call must use invitation
    language, and a sentence announcing someone who already joined is never a hiring signal."""
    q = (quote or "").strip()
    if not q or not _OPEN_CALL.search(q):
        return False
    if _NOT_HIRING.search(q) and not _re.search(r"\b(apply|applications?|prospective|recruit\w*|openings?)\b", q, _re.I):
        return False
    return True


_JOIN_LINK = _re.compile(r"\[([^\]]{2,80})\]\((https?://[^)\s]+)\)")
_JOIN_WORDS = _re.compile(r"\b(join|joining|openings?|positions?|opportunit\w*|prospective|vacanc\w*|hiring|recruit\w*|apply)\b", _re.I)


def join_links(page_text, base_url, limit=2):
    """Links on a lab/personal page that point to its own "Join us / Openings / Prospective
    students" page - the usual home of a hiring statement. Same site only."""
    host = urllib.parse.urlparse(base_url or "").netloc.lower()
    out = []
    for text, href in _JOIN_LINK.findall(page_text or ""):
        if _JOIN_WORDS.search(text) and urllib.parse.urlparse(href).netloc.lower() == host and href not in out:
            out.append(href)
    return out[:limit]


def _hiring_on_page(ai, p, inst, url, method):
    """Fetch one page, ask the model, verify the quote on the fetched text.
    Returns ("FOUND"|"UNCERTAIN"|"NONE"|"FAILED", record)."""
    page = fx.fetch_page(url)
    if not page.get("ok"):
        return "NONE", None
    o = ai.extract_hiring(page["text"], p["name"])
    if o.status == "RETRY_LATER":
        return "RETRY", None
    if o.status == "STAFF_REVIEW":
        return "FAILED", o
    if o.status == "VALID_EMPTY":
        # nothing on this page: follow its own "Join us / Openings" links once
        if method != "join_link":
            for sub in join_links(page["text"], page.get("url") or url):
                kind, rec = _hiring_on_page(ai, p, inst, sub, "join_link")
                if kind in ("FOUND", "UNCERTAIN", "RETRY"):
                    return kind, rec
        return "NONE", None
    f = o.value
    if not fx.quote_on_page(f.quote, page["text"]):
        return "NONE", None           # the model's sentence is not on the page: rejected
    if not hiring_sentence_ok(f.quote):
        return "NONE", None           # announcement / no invitation language: not a hiring signal
    if not hiring_quote_complete(f.quote) or hiring_expired(f.quote):
        return "NONE", None           # cut-off sentence, or an opening dated in the past
    v = ai.verify_hiring(f.quote, p["name"])
    if v.status == "RETRY_LATER":
        return "RETRY", None
    if v.status != "VALID_RESULT" or not v.value.is_open_recruitment:
        return "NONE", None           # second AI check: not a real open call
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
        log(f"Hiring check for {p['name']} skipped: no AI model configured.")
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
        if kind == "RETRY":
            log(f"Hiring check for {p['name']} postponed: AI models unavailable ({', '.join(m['model'] + ' ' + m['reason'] for m in ai.resting())}).")
            return None                # keep the previous result; re-checked on the next run
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
        if kind == "RETRY":
            return None
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
        fields.update(gated(p, match(p, inst_oid)))
        if fields.get("match_status") == "UNRESOLVED":
            fields.update(gated(p, resolve_unmatched(dict(p, **fields), inst, inst_oid) or {}))
    author = fields.get("openalex_author_id") or p.get("openalex_author_id")
    n_papers = len(p.get("paper_ids") or [])
    grants = p.get("grants") or []
    if author:
        q = dict(p, openalex_author_id=author)
        ids, subs, flds, awards = ingest_works(q)
        fields.update(paper_ids=ids, subfields=subs, fields=flds, last_openalex_update=st.now_iso())
        n_papers = len(ids)
    else:
        n_papers = len(fields.get("paper_ids") or p.get("paper_ids") or [])
    # still core faculty here? (adjunct/emeritus on the university's own page, or papers now elsewhere)
    try:
        fields.update(affiliation_fields(dict(p, **fields), inst))
    except fx.RateLimited:
        raise
    except Exception as e:
        log(f"Affiliation check skipped for {p['name']}: {str(e)[:120]}")
    if GRANTS():
        # every outcome (OpenAlex, Scholar, faculty page, unresolved): NSF/NIH by name and the
        # faculty page do not need an OpenAlex author
        try:
            grants, _c = collect_grants(dict(p, **fields), inst)
            fields.update(grants=grants, grant_count=len(grants), last_grant_update=st.now_iso())
        except fx.RateLimited:
            raise
        except Exception as e:
            # keep the professor's previous grants; staff can re-queue the grant search
            flag_staff_review("GRANT_SEARCH", "GRANT_SEARCH_FAILED", inst, p, source_url=p.get("faculty_url", ""),
                              extra={"last_error": f"{type(e).__name__}: {str(e)[:200]}", "department": p.get("department", "")})
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
        msg = run_next_job()                   # one-off jobs queued by staff/tests go first
        if msg:
            return msg
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
