"""One-time data cleanup for problems found in the audit (re-runnable; each step is idempotent).

    python -m services.cleanup [step ...]     steps: nonpersons depts benjaafar hiring grants identity papers counts

identity : re-check every OpenAlex match with pipe.identity_check; failures are unlinked (papers and
           subfields cleared, match UNRESOLVED, rejected id kept) and queued for a fresh, gated match.
papers   : re-fetch papers for remaining matches with type filter + de-duplication (pipe.clean_works).
"""
import sys
import time

from services import store as st
from services import pipe


def log(msg):
    print(f"[cleanup] {msg}", flush=True)


def nonpersons():
    d = st.db()
    n = 0
    for p in d.professors.find({}, {"name": 1, "institution_id": 1, "match_status": 1}):
        if not st.looks_like_person(p.get("name", "")):
            d.professors.delete_one({"_id": p["_id"]})
            n += 1
            log(f"removed non-person row: {p.get('name')!r} ({p['_id']})")
    log(f"nonpersons: removed {n}")


def depts():
    d = st.db()
    n = 0
    for p in d.professors.find({"department": {"$ne": ""}}, {"department": 1}):
        c = st.clean_department(p.get("department", ""))
        if c != p.get("department"):
            d.professors.update_one({"_id": p["_id"]}, {"$set": {"department": c}})
            n += 1
    log(f"depts: cleared {n} page-heading departments")


def benjaafar():
    """Same person listed at a former university whose row points at the CURRENT university's page."""
    d = st.db()
    n = 0
    for p in d.professors.find({"faculty_url": {"$ne": ""}}, {"faculty_url": 1, "institution_id": 1, "name": 1}):
        inst = st.get_institution(p["institution_id"]) or {}
        site = (inst.get("official_website") or "").lower().replace("https://", "").replace("http://", "").strip("/")
        host = site.split("/")[0].replace("www.", "")
        root = ".".join(host.split(".")[-2:]) if host else ""
        url = (p.get("faculty_url") or "").lower()
        if not root or root in url:
            continue
        # faculty page on another university's domain: a current row there for the same person?
        other = d.professors.find_one({"_id": {"$ne": p["_id"]}, "normalized_name": st.normalize_name(p["name"]),
                                       "faculty_url": p["faculty_url"]}, {"institution_id": 1})
        if other:
            d.professors.delete_one({"_id": p["_id"]})
            n += 1
            log(f"removed stale former-affiliation row: {p['name']} ({p['_id']}); current row {other['_id']}")
    log(f"benjaafar: removed {n} stale rows")


def hiring():
    d = st.db()
    n = 0
    for p in d.professors.find({"has_hiring": True}, {"name": 1, "hiring": 1}):
        h = p.get("hiring") or {}
        q = h.get("quote", "")
        reason = ""
        if pipe.hiring_expired(q):
            reason = "opening dated in the past"
        elif not pipe.hiring_quote_complete(q):
            reason = "quote is cut off"
        elif not h.get("source_url"):
            reason = "no source URL recorded"
        if reason:
            d.professors.update_one({"_id": p["_id"]}, {"$set": {
                "has_hiring": False, "hiring_status": "NO_PUBLIC_SIGNAL_FOUND",
                "hiring": {"status": "NO_PUBLIC_SIGNAL_FOUND", "quote": "", "source_url": "", "checked_at": st.now_iso(),
                           "rejected_quote": q, "rejected_reason": reason, "rejected_source": h.get("source_url", "")}}})
            pipe.queue_test_job("HIRING_CHECK", p["_id"], priority=2)
            n += 1
            log(f"hiring: {p['name']} -> withdrawn ({reason}); re-check queued")
    log(f"hiring: withdrew {n}")


def grants():
    d = st.db()
    canon_first, merged = {}, 0
    for g in d.grants.find({}, {"funder_name": 1, "funder_award_id": 1, "title": 1}).sort("_id", 1):
        c = pipe.grant_canon_key(g.get("funder_name"), g.get("funder_award_id"))
        if not c:
            continue
        d.grants.update_one({"_id": g["_id"]}, {"$set": {"canon_key": c}})
        if c not in canon_first:
            canon_first[c] = g["_id"]
            continue
        keep = canon_first[c]
        for p in d.professors.find({"grants.id": g["_id"]}, {"grants": 1}):
            links, seen = [], set()
            for l in p.get("grants") or []:
                gid = keep if l.get("id") == g["_id"] else l.get("id")
                if gid not in seen:
                    seen.add(gid)
                    links.append(dict(l, id=gid))
            d.professors.update_one({"_id": p["_id"]}, {"$set": {"grants": links, "grant_count": len(links)}})
        d.grants.delete_one({"_id": g["_id"]})
        merged += 1
        log(f"grants: merged duplicate {g['_id']} into {keep} ({c})")
    used = set()
    for p in d.professors.find({"grants.0": {"$exists": True}}, {"grants": 1}):
        used.update(l.get("id") for l in p.get("grants") or [])
    orphans = [g["_id"] for g in d.grants.find({}, {"_id": 1}) if g["_id"] not in used]
    if orphans:
        d.grants.delete_many({"_id": {"$in": orphans}})
    try:
        d.grants.create_index("canon_key", unique=True, partialFilterExpression={"canon_key": {"$type": "string"}})
    except Exception as e:
        log(f"grants: unique index not created ({str(e)[:120]})")
    log(f"grants: merged {merged} duplicates, removed {len(orphans)} orphans")


def identity():
    d = st.db()
    rows = list(d.professors.find({"match_status": "MATCHED", "openalex_author_id": {"$nin": ["", None]}},
                                  {"name": 1, "department": 1, "openalex_author_id": 1, "institution_id": 1}))
    log(f"identity: checking {len(rows)} matched professors")
    bad = 0
    for i, r in enumerate(rows):
        p = {"id": r["_id"], "name": r.get("name", ""), "department": r.get("department", ""),
             "institution_id": r.get("institution_id", "")}
        try:
            ok, note = pipe.identity_check(p, r["openalex_author_id"])
        except Exception as e:
            log(f"identity: {r.get('name')} check failed to run ({str(e)[:100]}); left as is")
            time.sleep(5)
            continue
        if not ok:
            bad += 1
            d.professors.update_one({"_id": r["_id"]}, {"$set": {
                "openalex_author_id": "", "match_status": "UNRESOLVED", "match_method": "",
                "match_note": "Identity check failed: " + note, "rejected_author_id": r["openalex_author_id"],
                "paper_ids": [], "subfields": [], "fields": [], "pipeline_done": False}})
            log(f"identity: UNLINKED {r.get('name')} ({r.get('department')}): {note}")
        if i % 100 == 0:
            log(f"identity: {i}/{len(rows)} checked, {bad} unlinked")
    log(f"identity: done, unlinked {bad} of {len(rows)}; they are re-processed by the pipeline with the gate on")


def papers():
    d = st.db()
    rows = list(d.professors.find({"match_status": "MATCHED", "openalex_author_id": {"$nin": ["", None]}},
                                  {"openalex_author_id": 1, "name": 1}))
    log(f"papers: re-fetching {len(rows)} paper lists with filtering + de-duplication")
    for i, r in enumerate(rows):
        try:
            ids, subs, flds, _aw = pipe.ingest_works({"openalex_author_id": r["openalex_author_id"]})
            d.professors.update_one({"_id": r["_id"]}, {"$set": {"paper_ids": ids, "subfields": subs, "fields": flds,
                                                                 "last_openalex_update": st.now_iso()}})
        except Exception as e:
            log(f"papers: {r.get('name')} skipped ({str(e)[:100]})")
            time.sleep(5)
        if i % 100 == 0:
            log(f"papers: {i}/{len(rows)}")
    log("papers: done")


def affiliation():
    """Current-affiliation check for every professor (page title + recent paper affiliations)."""
    d = st.db()
    rows = list(d.professors.find({"affiliation_checked": {"$exists": False}},
                                  {"name": 1, "department": 1, "faculty_url": 1, "institution_id": 1, "openalex_author_id": 1}))
    log(f"affiliation: checking {len(rows)} professors")
    counts_ = {}
    for i, r in enumerate(rows):
        p = st._clean(r)
        inst = st.get_institution(p["institution_id"]) or {}
        try:
            f = pipe.affiliation_fields(p, inst)
        except Exception as e:
            log(f"affiliation: {p.get('name')} skipped ({str(e)[:100]})")
            time.sleep(5)
            continue
        d.professors.update_one({"_id": r["_id"]}, {"$set": f})
        counts_[f["affiliation_status"]] = counts_.get(f["affiliation_status"], 0) + 1
        if f["affiliation_status"] != "CURRENT":
            log(f"affiliation: {p.get('name')} ({inst.get('name')}) -> {f['affiliation_status']}: {f['affiliation_note'][:160]}")
        if i % 100 == 0:
            log(f"affiliation: {i}/{len(rows)} {counts_}")
    log(f"affiliation: done {counts_}")


def counts():
    for inst in st.db().institutions.find({}, {"_id": 1}):
        st.recount(inst["_id"])
    st.invalidate_search()
    log("counts: every university recomputed from its professor rows")


STEPS = {"nonpersons": nonpersons, "depts": depts, "benjaafar": benjaafar, "hiring": hiring,
         "grants": grants, "identity": identity, "papers": papers, "affiliation": affiliation, "counts": counts}

if __name__ == "__main__":
    for name in (sys.argv[1:] or list(STEPS)):
        STEPS[name]()
    counts()
