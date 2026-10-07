"""The professor directory, stored directly in MongoDB (database "professor_atlas").

Why not the Jac graph: in this Jac release, adding edges to a graph reachable from
root gets quadratically slower (800 paper links took ~22 s, restoring ~2000 professors
never finished), which is what made search hang, the pipeline time out and restores
fail. MongoDB also survives sandbox resets, so there is nothing to back up or restore.

Collections (every document is plain JSON):
  institutions  _id = IPEDS UNITID (or "oa:<OpenAlex id>")
  professors    _id = "<institution _id>:<normalized name>"
  papers        _id = OpenAlex work id; "subfield"/"field" from the primary topic
  grants        _id = OpenAlex award id
  settings      small key/value docs (staff list, pipeline flags)
Links: professor.paper_ids, professor.grants [{id, role}], professor.hiring {...}.

Login accounts are NOT here: they live in the server's own account table
(.jac/data/users.db) and are mirrored to professor_atlas.accounts (accounts_mirror.py).
"""

import os
import re
import threading
import time
import unicodedata

from services import name_utils as nu

_LOCK = threading.Lock()
_DB = {"db": None, "uri": "", "checked": 0.0}
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _current_uri():
    # .env is re-read so a changed password/URI takes effect without restarting the server;
    # the process environment (set at startup) is only the fallback.
    try:
        with open(_ENV_FILE) as f:
            for line in f.read().splitlines():
                if line.strip().startswith("DIRECTORY_MONGODB_URI="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if v:
                        os.environ["DIRECTORY_MONGODB_URI"] = v
                        return v
    except OSError:
        pass
    return os.environ.get("DIRECTORY_MONGODB_URI", "").strip()


def configured():
    return bool(_current_uri())


def _connect(uri):
    from pymongo import MongoClient, ASCENDING
    client = MongoClient(uri, serverSelectionTimeoutMS=20000, retryWrites=True)
    client.admin.command("ping")              # fail now (bad password, network) instead of on first query
    d = client["professor_atlas"]
    d.professors.create_index([("institution_id", ASCENDING)])
    d.professors.create_index([("match_status", ASCENDING)])
    d.professors.create_index([("pipeline_done", ASCENDING)])
    d.institutions.create_index([("pipeline_state", ASCENDING)])
    return d


def db():
    # Reconnect when the URI changed (e.g. new password) - checked at most every 15 s.
    now = time.time()
    if _DB["db"] is not None and now - _DB["checked"] < 15:
        return _DB["db"]
    with _LOCK:
        uri = _current_uri()
        if not uri:
            raise RuntimeError("DIRECTORY_MONGODB_URI is not set (add it in Settings > Environment).")
        _DB["checked"] = now
        if _DB["db"] is None or uri != _DB["uri"]:
            old = _DB["db"]
            _DB["db"], _DB["uri"] = _connect(uri), uri
            if old is not None:
                try:
                    old.client.close()
                except Exception:
                    pass
    return _DB["db"]


def reset_connection():
    """Drop the cached client so the next db() call reconnects (used after an auth failure)."""
    with _LOCK:
        _DB["db"], _DB["uri"], _DB["checked"] = None, "", 0.0


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_name(name):
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace(".", " ").replace(",", " ").replace("-", " ")
    return " ".join(text.split())


def _clean(doc):
    if doc is None:
        return None
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    return d


# ---------------- institutions ----------------

INST_DEFAULTS = {
    "name": "", "city": "", "state": "", "official_website": "", "ipeds_id": "",
    "openalex_institution_id": "", "ror_id": "", "carnegie": "", "priority_tier": 4,
    "priority_rank": 999999, "priority_reason": "", "pipeline_state": "QUEUED", "pipeline_note": "",
    "directories": [], "dirs_checked": [], "n_professors": 0, "n_processed": 0, "n_matched": 0,
    "n_unresolved": 0, "n_papers": 0, "n_grants": 0, "n_hiring": 0,
}


def upsert_institution(iid, fields):
    """Create if missing (with defaults); only `fields` are overwritten on existing rows."""
    ts = now_iso()
    base = {k: v for k, v in INST_DEFAULTS.items() if k not in fields}
    base["created_at"] = ts
    db().institutions.update_one(
        {"_id": str(iid)},
        {"$setOnInsert": base, "$set": dict(fields, updated_at=ts)},
        upsert=True,
    )


def update_institution(iid, fields):
    db().institutions.update_one({"_id": str(iid)}, {"$set": dict(fields, updated_at=now_iso())})


def get_institution(iid):
    return _clean(db().institutions.find_one({"_id": str(iid)}))


def list_institutions(query=None, sort_queue=True):
    cur = db().institutions.find(query or {})
    rows = [_clean(d) for d in cur]
    if sort_queue:
        rows.sort(key=lambda r: (int(r.get("priority_tier", 4)), int(r.get("priority_rank", 999999)), r.get("name", "")))
    return rows


def count_institutions(query=None):
    return db().institutions.count_documents(query or {})


# ---------------- professors ----------------

PROF_DEFAULTS = {
    "name": "", "normalized_name": "", "title": "", "department": "", "institution_id": "",
    "university": "", "faculty_url": "", "directory_url": "", "lab_url": "", "personal_url": "", "openalex_author_id": "",
    "orcid": "", "match_status": "PENDING", "match_method": "", "match_note": "", "pipeline_done": False,
    "profile_extracted": False, "anchors": [], "paper_ids": [], "subfields": [], "fields": [],
    "grants": [], "grant_count": 0, "hiring": None, "has_hiring": False, "search_text": "",
    "last_openalex_update": "", "last_grant_update": "", "last_hiring_update": "",
}


def prof_id(inst_id, name):
    return f"{inst_id}:{nu.storage_key(name)}"


def search_text_for(p):
    parts = [p.get("name", ""), p.get("title", ""), p.get("department", ""), p.get("university", "")]
    parts += list(p.get("fields") or []) + list(p.get("subfields") or [])
    return normalize_name(" | ".join(parts))


# Words that are never part of a person's name. ("Dean", "Chancellor", "Head" are real surnames -
# Brian K. Dean, Dean Yang, Stevie Chancellor - so they are only a role when combined with these.)
_ROLE_WORDS = re.compile(
    r"\b(chair|provost|director|coordinator|interim|faculty|staff|directory|department|office|media|"
    r"news|research|about|team|program|center|institute|school|college|graduate|undergraduate|studies|"
    r"awards?|bookshelf|highlights|affairs|area)\b", re.I)
_TRAILING_ROLE = re.compile(r"\s+(chair|visiting|emerit\w*|director|dean)$", re.I)


def repair_name(name):
    """Undo common scraper doubling: "Benjamin Bakker Benjamin Bakker" -> "Benjamin Bakker",
    "Frank Merle Frank Merle Visiting" -> "Frank Merle". Anything else is returned unchanged."""
    n = " ".join((name or "").split())
    words = n.split()
    for k in range(len(words) // 2, 1, -1):
        if words[:k] == words[k:2 * k]:
            rest = " ".join(words[2 * k:])
            if not rest or _TRAILING_ROLE.match(" " + rest):
                return " ".join(words[:k])
    return n
_HEADING_DEPTS = re.compile(
    r"^(?:index\.(?:php|html?)|faculty\.(?:php|html?)|faculty(?:\s*(?:and|&)\s*staff)?(?:\s*directory)?|faculty staff|"
    r"faculty and staff(?: directory)?|faculty & staff(?: directory)?|meet (?:our|the) faculty|"
    r"our (?:faculty|team|people)|all faculty|find faculty|faculty by department|faculty at .+|"
    r"additional faculty|people(?: directory)?|find faculty|who we are|about(?: us)?|all|group|apply|www|research(?: highlights)?|faculty research|"
    r"faculty (?:and|&) research|deans? awards?|faculty bookshelf|people directory|directory|home|news|in the media|"
    r"academic seminars|www|all|group|apply|about)$", re.I)


def looks_like_person(name):
    """Reject page headings/roles and canonicalize credential-decorated names."""
    n = repair_name(nu.clean_person_name(name))
    if not nu.looks_like_person(n):
        return False
    if n.lower().startswith(("from ", "by ", "the ", "in ")):
        return False
    if _ROLE_WORDS.search(n):
        return False
    low = n.lower()
    if re.search(r"\b(dean|chancellor|president|head)\b", low) and re.search(r"\b(associate|assistant|vice|executive|senior|academic)\b", low):
        return False
    return True


def clean_department(department):
    """A page heading ("Meet Our Faculty", "Faculty & Staff Directory") is not a department."""
    d = " ".join((department or "").split())
    return "" if _HEADING_DEPTS.match(d) else d


def add_professor(inst, name, title, department, faculty_url, directory_url=""):
    """Insert a crawled professor; on a re-crawl, repair weak old metadata in-place.

    faculty_url must be the person's own profile. directory_url records the listing page that
    discovered them; the two are deliberately separate so a college-wide directory is never
    mistaken for a professor's personal faculty page.

    This matters after directory-discovery improvements: a row originally stored with a generic
    department such as "Cis" / "Faculty Directory" should learn the specific department when the
    same professor is seen again.
    """
    from pymongo.errors import DuplicateKeyError
    if not looks_like_person(name):
        return False
    name = repair_name(nu.clean_person_name(name))
    if not name:
        return False
    department = clean_department(department)
    pid = prof_id(inst["id"], name)
    doc = dict(PROF_DEFAULTS)
    ts = now_iso()
    doc.update(
        _id=pid, name=name.strip(), normalized_name=nu.storage_key(name), title=title or "Professor",
        department=department, institution_id=inst["id"], university=inst["name"],
        faculty_url=faculty_url or "", directory_url=directory_url or "",
        created_at=ts, updated_at=ts,
    )
    doc["search_text"] = search_text_for(doc) + " | " + normalize_name(inst.get("city", "") + " " + inst.get("state", ""))
    try:
        db().professors.insert_one(doc)
        return True
    except DuplicateKeyError:
        old = db().professors.find_one({"_id": pid}) or {}
        patch = {}
        old_dept = " ".join((old.get("department") or "").split())
        new_dept = " ".join((department or "").split())
        old_generic = not clean_department(old_dept)
        # Short site/acronym labels ("Cis", "Cse") are also weaker than a real department name.
        if new_dept and (not old_dept or old_generic or (len(old_dept) <= 5 and len(new_dept) > len(old_dept))):
            patch["department"] = new_dept
        old_title = (old.get("title") or "").strip()
        new_title = (title or "").strip()
        if new_title and (not old_title or (old_title.lower() == "professor" and new_title.lower() != "professor")):
            patch["title"] = new_title
        if faculty_url and not old.get("faculty_url"):
            patch["faculty_url"] = faculty_url
        if directory_url and not old.get("directory_url"):
            patch["directory_url"] = directory_url
        if patch:
            merged = dict(old, **patch)
            patch["search_text"] = search_text_for(merged) + " | " + normalize_name(
                inst.get("city", "") + " " + inst.get("state", ""))
            update_professor(pid, patch)
        return False


def update_professor(pid, fields):
    db().professors.update_one({"_id": pid}, {"$set": dict(fields, updated_at=now_iso())})


def get_professor(pid):
    return _clean(db().professors.find_one({"_id": pid}))


def collapse_author_duplicates(inst_id, author_id):
    """Merge same-person duplicate cards after a confirmed OpenAlex match.

    Only rows at the same university whose names are mutually compatible are merged. If names
    conflict, identity_check/cleanup reopens them instead; this function never guesses.
    Returns the surviving professor id, or "" when no safe merge was possible.
    """
    if not author_id:
        return ""
    d = db()
    rows = list(d.professors.find({"institution_id": inst_id, "openalex_author_id": author_id}))
    if len(rows) < 2:
        return str(rows[0]["_id"]) if rows else ""
    if not all(nu.names_match_strict(a.get("name", ""), b.get("name", ""))
               for i, a in enumerate(rows) for b in rows[i + 1:]):
        return ""

    def score(r):
        return (len(nu.name_tokens(r.get("name", ""))), len(r.get("name", "")),
                int(bool(r.get("faculty_url"))), len(r.get("paper_ids") or []))

    winner = max(rows, key=score)
    losers = [r for r in rows if r["_id"] != winner["_id"]]
    merged = dict(winner)

    depts = []
    urls = []
    papers = []
    grants = []
    grant_seen = set()
    for r in rows:
        for dep in (r.get("department") or "").split(";"):
            dep = dep.strip()
            if dep and dep not in depts:
                depts.append(dep)
        for u in [r.get("faculty_url")] + list(r.get("alternate_faculty_urls") or []):
            if u and u not in urls:
                urls.append(u)
        for pid in r.get("paper_ids") or []:
            if pid not in papers:
                papers.append(pid)
        for g in r.get("grants") or []:
            gid = g.get("id")
            if gid and gid not in grant_seen:
                grant_seen.add(gid)
                grants.append(g)

    if depts:
        merged["department"] = "; ".join(depts)
    if urls:
        merged["faculty_url"] = merged.get("faculty_url") or urls[0]
        merged["alternate_faculty_urls"] = [u for u in urls if u != merged["faculty_url"]]
    merged["paper_ids"] = papers
    merged["grants"] = grants
    merged["grant_count"] = len(grants)
    merged["search_text"] = search_text_for(merged)
    merged["updated_at"] = now_iso()
    d.professors.replace_one({"_id": winner["_id"]}, merged)
    loser_ids = [r["_id"] for r in losers]
    d.professors.delete_many({"_id": {"$in": loser_ids}})
    d.staff_review.delete_many({"professor_id": {"$in": loser_ids}})
    invalidate_search()
    return str(winner["_id"])


def next_pending_professor(inst_id):
    """Next runnable professor; transient identity failures are delayed instead of hot-looping."""
    now = time.time()
    q = {"institution_id": inst_id, "pipeline_done": False,
         "$or": [{"identity_retry_after": {"$exists": False}},
                 {"identity_retry_after": {"$lte": now}},
                 {"identity_retry_after": 0}]}
    return _clean(db().professors.find_one(q, sort=[("_id", 1)]))


def count_professors(query=None):
    return db().professors.count_documents(query or {})


def professors_of(inst_id, fields=None):
    return [_clean(d) for d in db().professors.find({"institution_id": inst_id}, fields)]


# ---------------- papers / grants ----------------

def upsert_paper(doc):
    wid = doc["openalex_work_id"]
    ts = now_iso()
    db().papers.update_one(
        {"_id": wid},
        {"$set": dict({k: v for k, v in doc.items() if k != "openalex_work_id"}, updated_at=ts),
         "$setOnInsert": {"created_at": ts}},
        upsert=True,
    )


def papers_by_ids(ids):
    if not ids:
        return []
    return [_clean(d) for d in db().papers.find({"_id": {"$in": list(ids)}})]


def upsert_grant(doc):
    gid = doc["openalex_award_id"]
    ts = now_iso()
    from services.pipe import grant_canon_key
    canon = grant_canon_key(doc.get("funder_name"), doc.get("funder_award_id"))
    if canon:
        doc = dict(doc, canon_key=canon)
    db().grants.update_one(
        {"_id": gid},
        {"$set": dict({k: v for k, v in doc.items() if k != "openalex_award_id"}, updated_at=ts),
         "$setOnInsert": {"created_at": ts}},
        upsert=True,
    )


def grants_by_ids(ids):
    if not ids:
        return []
    return [_clean(d) for d in db().grants.find({"_id": {"$in": list(ids)}})]


# ---------------- counters ----------------

def recount(inst_id):
    """Refresh one university's cached counters (cheap aggregation, no graph walks)."""
    pipe = [
        {"$match": {"institution_id": inst_id}},
        {"$group": {
            "_id": None,
            "n": {"$sum": 1},
            "done": {"$sum": {"$cond": ["$pipeline_done", 1, 0]}},
            "matched": {"$sum": {"$cond": [{"$eq": ["$match_status", "MATCHED"]}, 1, 0]}},
            "unresolved": {"$sum": {"$cond": [{"$in": ["$match_status", ["UNRESOLVED", "NO_RESULT_FOUND"]]}, 1, 0]}},
            "papers": {"$sum": {"$size": {"$ifNull": ["$paper_ids", []]}}},
            "grants": {"$sum": {"$ifNull": ["$grant_count", 0]}},
            "hiring": {"$sum": {"$cond": ["$has_hiring", 1, 0]}},
        }},
    ]
    r = next(iter(db().professors.aggregate(pipe)), None) or {}
    update_institution(inst_id, {
        "n_professors": r.get("n", 0), "n_processed": r.get("done", 0), "n_matched": r.get("matched", 0),
        "n_unresolved": r.get("unresolved", 0), "n_papers": r.get("papers", 0),
        "n_grants": r.get("grants", 0), "n_hiring": r.get("hiring", 0),
    })


# ---------------- search ----------------

_SEARCH = {"t": 0.0, "rows": [], "busy": False}
SEARCH_FIELDS = {"name": 1, "title": 1, "department": 1, "university": 1, "subfields": 1, "fields": 1,
                 "grant_count": 1, "has_hiring": 1, "match_status": 1, "search_text": 1, "affiliation_status": 1}


def invalidate_search():
    _SEARCH["t"] = 0.0


def search_rows():
    """All professors as light rows, cached in memory for 60 s (one Mongo read ~ <1 s)."""
    now = time.time()
    if _SEARCH["rows"] and now - _SEARCH["t"] < 60:
        return _SEARCH["rows"]
    rows = [_clean(d) for d in db().professors.find({}, SEARCH_FIELDS)]
    rows.sort(key=lambda r: r.get("name", ""))
    _SEARCH["rows"] = rows
    _SEARCH["t"] = now
    return rows


def search(query="", university="", department="", subfield="", has_grant=False, has_hiring=False, offset=0, limit=50):
    words = [w for w in normalize_name(query).split() if w]
    # Universities name departments differently ("Computer Science and Engineering",
    # "Electrical Engineering and Computer Sciences"); the filter matches by contained name.
    dept = normalize_name(department)
    out = []
    for r in search_rows():
        # adjunct / emeritus / moved-on people stay in the database (profile page reachable by link)
        # but are not listed as this university's faculty
        if r.get("affiliation_status") in ("NOT_CORE", "LIKELY_MOVED"):
            continue
        if university and r.get("university") != university:
            continue
        if dept and dept not in normalize_name(r.get("department", "")):
            continue
        if subfield and subfield not in (r.get("subfields") or []) and subfield not in (r.get("fields") or []):
            continue
        if has_grant and not r.get("grant_count"):
            continue
        if has_hiring and not r.get("has_hiring"):
            continue
        if words:
            hay = r.get("search_text", "")
            if not all(w in hay for w in words):
                continue
        out.append(r)
    start = max(0, int(offset))
    size = max(1, min(int(limit), 200))
    return {"items": out[start:start + size], "total": len(out), "offset": start}


def filter_options(university="", department=""):
    """Universities: always all. Departments: only those the selected university has.
    Fields/subfields: only those of professors in the selected university + department."""
    rows = search_rows()
    unis = sorted({r.get("university", "") for r in rows if r.get("university")})
    if university:
        rows = [r for r in rows if r.get("university") == university]
    depts = sorted({r.get("department", "") for r in rows if r.get("department")})
    dept = normalize_name(department)
    if dept:
        rows = [r for r in rows if dept in normalize_name(r.get("department", ""))]
    subs = sorted({s for r in rows for s in (r.get("subfields") or [])})
    flds = sorted({f for r in rows for f in (r.get("fields") or [])})
    return {"universities": unis, "departments": depts, "subfields": subs, "fields": flds}


# ---------------- staff review ----------------

def staff_review_items(limit=200):
    """Open items the system could not finish on its own, newest first."""
    rows = db().staff_review.find({"resolved": False}).sort("attempted_at", -1).limit(int(limit))
    return [dict(d, id=str(d.pop("_id"))) for d in rows]


def resolve_staff_review(item_id):
    db().staff_review.update_one({"_id": item_id}, {"$set": {"resolved": True, "resolved_at": now_iso()}})


# ---------------- settings ----------------

def get_setting(key, default=None):
    d = db().settings.find_one({"_id": key})
    return d.get("value", default) if d else default


def set_setting(key, value):
    db().settings.update_one({"_id": key}, {"$set": {"value": value, "updated_at": now_iso()}}, upsert=True)


def prune_transient(days_search=30, days_jobs=14, days_review=30):
    """Delete rebuildable operational records so Atlas Free storage does not grow forever.

    Core data (institutions, professors, papers, grants, accounts) is never touched.
    """
    d = db()
    now = time.time()
    search_cut = now - int(days_search) * 86400
    job_cut = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - int(days_jobs) * 86400))
    review_cut = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - int(days_review) * 86400))
    a = d.search_cache.delete_many({"t": {"$lt": search_cut}}).deleted_count
    b = d.jobs.delete_many({"status": {"$in": ["DONE", "FAILED"]}, "finished_at": {"$lt": job_cut}}).deleted_count
    c = d.staff_review.delete_many({"resolved": True, "resolved_at": {"$lt": review_cut}}).deleted_count
    return {"search_cache": int(a), "jobs": int(b), "staff_review": int(c)}


def overview():
    d = db()
    return {
        "institutions": d.institutions.count_documents({}),
        "professors": d.professors.count_documents({}),
        "matched": d.professors.count_documents({"match_status": "MATCHED"}),
        "unresolved": d.professors.count_documents({"match_status": "UNRESOLVED"}),
        "papers": d.papers.estimated_document_count(),
        "grants": d.professors.count_documents({"grant_count": {"$gt": 0}}),
        "hiring": d.professors.count_documents({"has_hiring": True}),
    }


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
