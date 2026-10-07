"""One-time data cleanup for problems found in the audit (re-runnable; each step is idempotent).

    python -m services.cleanup [step ...]
    common repair after upgrading:
      python -m services.cleanup upgrade_safe

    steps: names directory_profiles retry_identity orcid_conflicts profile_rescan author_duplicates states nonpersons
           depts benjaafar hiring grants grant_recheck identity papers paper_orphans affiliation counts

identity : re-check every OpenAlex match with pipe.identity_check; failures are unlinked (papers and
           subfields cleared, match UNRESOLVED, rejected id kept) and queued for a fresh, gated match.
papers   : re-fetch papers for remaining matches with type filter + de-duplication (pipe.clean_works).
"""
import sys
import time

from services import store as st
from services import pipe
from services import name_utils as nu


def log(msg):
    print(f"[cleanup] {msg}", flush=True)


def _identity_quality(p):
    rank = {"MATCHED": 6, "SCHOLAR": 5, "FACULTY_PAGE": 4, "PENDING": 3,
            "UNRESOLVED": 2, "NO_RESULT_FOUND": 1}
    return (rank.get(p.get("match_status"), 0), int(bool(p.get("pipeline_done"))),
            len(p.get("paper_ids") or []), int(bool(p.get("faculty_url"))))


def _reset_identity_doc(doc, previous_note=""):
    """Make a row eligible for the new Scholar-first identity ladder without losing its source page."""
    if previous_note:
        doc["previous_match_note"] = previous_note
    doc.update(openalex_author_id="", match_status="PENDING", match_method="", match_note="",
               pipeline_done=False, profile_extracted=False, identity_retry_after=0,
               paper_ids=[], subfields=[], fields=[])
    for k in ("profile_full_scan_at", "identity_checked", "identity_steps", "scholar_checked",
              "orcid_checked", "llm_papers_checked", "llm_papers_ai", "scholar_url",
              "scholar_id", "scholar_affiliation", "rejected_author_id"):
        doc.pop(k, None)


def names():
    """Canonicalize stored professor names and re-key rows after stripping credentials.

    Examples: "A.J. Bauer, Ph.D." -> "A.J. Bauer", "MD Ari Blitz" -> "Ari Blitz".
    Rows whose earlier unresolved/faculty-page result was produced with a decorated name are
    reopened so the new Google-Scholar-first pipeline can identify them correctly.
    """
    d = st.db()
    rows = list(d.professors.find({}))
    renamed = rekeyed = removed = reopened = merged = 0
    affected_insts = set()

    for p in rows:
        old_id = p["_id"]
        raw = p.get("name", "")
        clean = nu.clean_person_name(raw)
        if not clean or not st.looks_like_person(clean):
            d.professors.delete_one({"_id": old_id})
            d.staff_review.delete_many({"professor_id": old_id})
            d.jobs.delete_many({"professor_id": old_id, "status": {"$in": ["QUEUED", "RUNNING"]}})
            removed += 1
            affected_insts.add(p.get("institution_id", ""))
            log(f"names: removed non-person row {raw!r} ({old_id})")
            continue

        new_id = st.prof_id(p.get("institution_id", ""), clean)
        changed_name = clean != raw
        doc = dict(p)
        doc["_id"] = new_id
        doc["name"] = clean
        doc["normalized_name"] = nu.storage_key(clean)
        if changed_name:
            renamed += 1
            affected_insts.add(p.get("institution_id", ""))
            if p.get("match_status") in ("UNRESOLVED", "NO_RESULT_FOUND", "FACULTY_PAGE"):
                _reset_identity_doc(doc, p.get("match_note", ""))
                reopened += 1

        if new_id == old_id:
            if changed_name or p.get("normalized_name") != doc["normalized_name"]:
                d.professors.replace_one({"_id": old_id}, doc)
            continue

        existing = d.professors.find_one({"_id": new_id})
        if existing:
            # Same canonical person was scraped twice (usually once with credentials). Keep the
            # better completed record, but retain the official source URL when only one has it.
            winner = doc if _identity_quality(doc) > _identity_quality(existing) else dict(existing)
            loser = existing if winner is doc else doc
            if not winner.get("faculty_url") and loser.get("faculty_url"):
                winner["faculty_url"] = loser["faculty_url"]
            winner["_id"] = new_id
            winner["name"] = clean
            winner["normalized_name"] = nu.storage_key(clean)
            d.professors.replace_one({"_id": new_id}, winner, upsert=True)
            merged += 1
        else:
            d.professors.insert_one(doc)
            rekeyed += 1
        d.professors.delete_one({"_id": old_id})
        d.staff_review.delete_many({"professor_id": old_id})
        d.jobs.delete_many({"professor_id": old_id, "status": {"$in": ["QUEUED", "RUNNING"]}})

    for iid in affected_insts:
        if iid:
            # A reopened row must be visible to the worker even when the institution was DONE.
            if d.professors.count_documents({"institution_id": iid, "pipeline_done": False}):
                d.institutions.update_one({"_id": iid}, {"$set": {"pipeline_state": "PROCESSING",
                                                                  "pipeline_note": "Professor names/identity evidence repaired; reprocessing pending rows."}})
            st.recount(iid)
    st.invalidate_search()
    log(f"names: renamed {renamed}, re-keyed {rekeyed}, merged {merged}, removed {removed}, reopened {reopened}")


def directory_profiles():
    """Repair faculty_url values that are not individual professor profiles.

    Older crawls used (profile_url or directory_url), so unlinked directory layouts could make
    dozens of professors share one fake faculty page. Social-icon links could also be mistaken
    for a profile. Both are unsafe identity evidence and are cleared before reprocessing.
    """
    import urllib.parse
    d = st.db()
    dir_urls = set()
    for inst in d.institutions.find({}, {"directories": 1}):
        for pair in inst.get("directories") or []:
            if isinstance(pair, list) and len(pair) >= 2 and pair[1]:
                dir_urls.add(str(pair[1]).rstrip("/"))
    bad_hosts = {"x.com", "twitter.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com"}
    rows = []
    for p in d.professors.find({"faculty_url": {"$nin": ["", None]}},
                               {"faculty_url": 1, "institution_id": 1, "name": 1}):
        url = str(p.get("faculty_url") or "")
        host = urllib.parse.urlparse(url).netloc.lower()
        is_directory = url.rstrip("/") in dir_urls
        is_social = any(host == h or host.endswith("." + h) for h in bad_hosts)
        if is_directory or is_social:
            rows.append((p, is_directory, is_social))

    insts = set()
    n_dir = n_social = 0
    for p, is_directory, is_social in rows:
        insts.add(p.get("institution_id", ""))
        old = p.get("faculty_url") or ""
        set_fields = {"faculty_url": "", "profile_extracted": False,
                      "pipeline_done": False, "identity_retry_after": 0}
        if is_directory:
            set_fields["directory_url"] = old
            n_dir += 1
        if is_social:
            set_fields["rejected_profile_url"] = old
            n_social += 1
        d.professors.update_one({"_id": p["_id"]}, {
            "$set": set_fields,
            "$unset": {
                "profile_full_scan_at": "", "scholar_checked": "", "scholar_url": "",
                "scholar_id": "", "scholar_affiliation": "", "scholar_link_candidates": "",
                "scholar_link_urls": "", "anchors": "",
            }
        })
    for iid in insts:
        if iid:
            d.institutions.update_one({"_id": iid}, {"$set": {
                "pipeline_state": "PROCESSING",
                "pipeline_note": "Invalid/shared faculty profile URLs repaired; affected identities are being rechecked."
            }})
            st.recount(iid)
    st.invalidate_search()
    log(f"directory_profiles: repaired {n_dir} shared directory URLs and {n_social} social URLs across {len([x for x in insts if x])} universities")

def retry_identity():
    """Repair rows that were incorrectly finalized after an old transient identity failure.

    The previous process_professor() converted RETRY_LATER into an empty dict and then set
    pipeline_done=True.  Those rows have no completed identity_steps/scholar check. Reopen them.
    """
    d = st.db()
    q = {"pipeline_done": True, "match_status": {"$in": ["UNRESOLVED", "NO_RESULT_FOUND"]},
         "$or": [{"identity_steps": {"$exists": False}}, {"identity_steps": []}]}
    rows = list(d.professors.find(q, {"institution_id": 1, "name": 1, "match_note": 1}))
    insts = set()
    for r in rows:
        insts.add(r.get("institution_id", ""))
        update = {"$set": {
            "previous_match_note": r.get("match_note", ""), "openalex_author_id": "",
            "match_status": "PENDING", "match_method": "", "match_note": "",
            "pipeline_done": False, "profile_extracted": False, "identity_retry_after": 0,
            "paper_ids": [], "subfields": [], "fields": [],
        }, "$unset": {
            "profile_full_scan_at": "", "identity_checked": "", "identity_steps": "",
            "scholar_checked": "", "orcid_checked": "", "llm_papers_checked": "",
            "llm_papers_ai": "", "rejected_author_id": "",
        }}
        d.professors.update_one({"_id": r["_id"]}, update)
    for iid in insts:
        if iid:
            d.institutions.update_one({"_id": iid}, {"$set": {"pipeline_state": "PROCESSING",
                                                              "pipeline_note": "Re-running incomplete Scholar/identity checks."}})
            st.recount(iid)
    st.invalidate_search()
    log(f"retry_identity: reopened {len(rows)} prematurely finalized professor rows across {len([x for x in insts if x])} universities")


def profile_rescan():
    """Re-run legacy completed rows through the new full-page, Scholar-first identity order.

    Old rows remain visible while pending: existing papers/matches are not erased here. The normal
    processor re-reads the complete official page, tries its Scholar link/search first, then page
    publications, and replaces stale weak identity conclusions only after new evidence is checked.
    """
    d = st.db()
    q = {"pipeline_done": True, "profile_full_scan_at": {"$exists": False}}
    rows = list(d.professors.find(q, {"institution_id": 1}))
    insts = {r.get("institution_id", "") for r in rows if r.get("institution_id")}
    if rows:
        ids = [r["_id"] for r in rows]
        for i in range(0, len(ids), 1000):
            d.professors.update_many({"_id": {"$in": ids[i:i + 1000]}},
                                     {"$set": {"pipeline_done": False, "identity_retry_after": 0}})
    for iid in insts:
        d.institutions.update_one({"_id": iid}, {"$set": {
            "pipeline_state": "PROCESSING",
            "pipeline_note": "Re-scanning official faculty pages with Scholar-first identity logic."
        }})
        st.recount(iid)
    st.invalidate_search()
    log(f"profile_rescan: reopened {len(rows)} legacy processed rows across {len(insts)} universities")


def author_duplicates():
    """Repair duplicate professor cards that point at the same OpenAlex author at one university.

    Compatible name variants are merged (Erin Cech / Erin A. Cech). Conflicting names are all
    reopened so the current strict identity gate decides again instead of preserving a bad link.
    """
    d = st.db()
    groups = {}
    for p in d.professors.find({"match_status": "MATCHED", "openalex_author_id": {"$nin": ["", None]}}):
        key = (p.get("institution_id", ""), p.get("openalex_author_id", ""))
        groups.setdefault(key, []).append(p)
    merged = reopened = 0
    touched = set()
    for (iid, aid), rows in groups.items():
        if len(rows) < 2:
            continue
        compatible = all(nu.names_match_strict(a.get("name", ""), b.get("name", ""))
                         for i, a in enumerate(rows) for b in rows[i + 1:])
        if compatible:
            before = d.professors.count_documents({"institution_id": iid, "openalex_author_id": aid})
            st.collapse_author_duplicates(iid, aid)
            after = d.professors.count_documents({"institution_id": iid, "openalex_author_id": aid})
            merged += max(0, before - after)
            touched.add(iid)
            continue

        # Old loose first-initial matching could map two different names to one author. Remove the
        # conclusion from every conflicting row; the new page/Scholar/strict-name pipeline retries.
        for r in rows:
            d.professors.update_one({"_id": r["_id"]}, {"$set": {
                "previous_match_note": r.get("match_note", ""),
                "openalex_author_id": "", "match_status": "PENDING", "match_method": "", "match_note": "",
                "pipeline_done": False, "identity_retry_after": 0,
                "paper_ids": [], "subfields": [], "fields": [], "profile_extracted": False,
            }, "$unset": {"identity_checked": "", "identity_steps": "", "profile_full_scan_at": "",
                           "scholar_checked": "", "rejected_author_id": ""}})
            reopened += 1
        touched.add(iid)
        log(f"author_duplicates: reopened conflicting author {aid}: "
            + ", ".join(r.get("name", "") for r in rows))

    for iid in touched:
        if iid:
            d.institutions.update_one({"_id": iid}, {"$set": {
                "pipeline_state": "PROCESSING",
                "pipeline_note": "Duplicate author identities repaired; reprocessing affected rows."
            }})
            st.recount(iid)
    st.invalidate_search()
    log(f"author_duplicates: merged {merged} duplicate cards; reopened {reopened} conflicting rows")


def orcid_conflicts():
    """Clear ORCID ids that are attached to incompatible professor names.

    Same-person duplicate cards (e.g. Erin Cech / Erin A. Cech) are allowed. A single ORCID on
    genuinely different names is unsafe evidence and usually came from an old whole-page scrape.
    Those rows are reopened so the official-page/Scholar/ORCID pipeline can rebuild identity.
    """
    d = st.db()
    groups = {}
    for p in d.professors.find({"orcid": {"$nin": ["", None]}},
                               {"name": 1, "orcid": 1, "institution_id": 1, "match_status": 1,
                                "match_note": 1, "pipeline_done": 1}):
        groups.setdefault(p.get("orcid", ""), []).append(p)

    reopened = 0
    touched = set()
    for oid, rows in groups.items():
        if len(rows) < 2:
            continue
        compatible = all(nu.names_match_strict(a.get("name", ""), b.get("name", ""))
                         for i, a in enumerate(rows) for b in rows[i + 1:])
        if compatible:
            continue
        for r in rows:
            update = {
                "$set": {
                    "previous_orcid": oid,
                    "orcid": "",
                    "pipeline_done": False,
                    "profile_extracted": False,
                    "identity_retry_after": 0,
                },
                "$unset": {
                    "profile_full_scan_at": "", "orcid_checked": "", "orcid_source": "",
                    "orcid_candidate": "", "orcid_reject_note": "",
                },
            }
            # If the bad ORCID participated in an unresolved/weak result, reopen identity entirely.
            if r.get("match_status") in ("UNRESOLVED", "NO_RESULT_FOUND", "PENDING"):
                update["$set"].update({
                    "openalex_author_id": "", "match_status": "PENDING",
                    "match_method": "", "match_note": "",
                    "paper_ids": [], "subfields": [], "fields": [],
                })
                update["$unset"].update({
                    "identity_checked": "", "identity_steps": "", "scholar_checked": "",
                    "llm_papers_checked": "", "llm_papers_ai": "", "rejected_author_id": "",
                })
            d.professors.update_one({"_id": r["_id"]}, update)
            touched.add(r.get("institution_id", ""))
            reopened += 1
        log(f"orcid_conflicts: cleared shared incompatible ORCID {oid}: "
            + ", ".join(r.get("name", "") for r in rows))

    for iid in touched:
        if iid:
            d.institutions.update_one({"_id": iid}, {"$set": {
                "pipeline_state": "PROCESSING",
                "pipeline_note": "Conflicting ORCID evidence repaired; affected professors are being reprocessed."
            }})
            st.recount(iid)
    st.invalidate_search()
    log(f"orcid_conflicts: reopened {reopened} professor rows across {len([x for x in touched if x])} universities")


def states():
    """A DONE university must not contain unprocessed professor rows."""
    d = st.db()
    repaired = 0
    pending_ids = d.professors.distinct("institution_id", {"pipeline_done": False})
    for iid in pending_ids:
        inst = d.institutions.find_one({"_id": iid}, {"pipeline_state": 1})
        if inst and inst.get("pipeline_state") == "DONE":
            d.institutions.update_one({"_id": iid}, {"$set": {
                "pipeline_state": "PROCESSING",
                "pipeline_note": "Pending professor rows found during consistency audit; processing resumed."
            }})
            repaired += 1
    log(f"states: reopened {repaired} DONE universities that still had pending professors")


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
    insts = set()
    for p in d.professors.find({"department": {"$ne": ""}}, {"department": 1, "institution_id": 1}):
        c = st.clean_department(p.get("department", ""))
        if c != p.get("department"):
            d.professors.update_one({"_id": p["_id"]}, {"$set": {"department": c}})
            insts.add(p.get("institution_id", ""))
            n += 1
    # Re-crawl affected universities once. add_professor() now repairs metadata on duplicate IDs,
    # so a real department discovered on the new pass replaces the cleared generic page label.
    for iid in insts:
        if iid:
            d.institutions.update_one({"_id": iid}, {"$set": {
                "pipeline_state": "CRAWLING",
                "pipeline_note": "Generic department labels cleared; re-crawling faculty metadata."
            }})
    log(f"depts: cleared {n} page-heading departments; queued {len([x for x in insts if x])} universities for metadata re-crawl")


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
        other = d.professors.find_one({"_id": {"$ne": p["_id"]}, "normalized_name": nu.storage_key(p["name"]),
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


def grant_recheck():
    """Queue a fresh grant rebuild for every professor that currently has stored grant links.

    This is intentionally NOT part of upgrade_safe: it calls external NSF/NIH/OpenAlex sources
    through the normal background worker. Use it after identity/name repairs settle. Each job
    replaces the professor's grant list with results from the current strict investigator +
    institution rules, which removes stale links created by older matching logic.
    """
    d = st.db()
    n = 0
    for p in d.professors.find({"grant_count": {"$gt": 0}}, {"_id": 1}):
        pipe.queue_test_job("GRANT_CHECK", str(p["_id"]), priority=1)
        n += 1
    log(f"grant_recheck: queued {n} professors with existing grant links for current-rule validation")


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
    # resumable: lists already re-fetched in the last day (by an interrupted run) are skipped
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    rows = list(d.professors.find({"match_status": "MATCHED", "openalex_author_id": {"$nin": ["", None]},
                                   "last_openalex_update": {"$not": {"$gte": since}}},
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


def paper_orphans():
    """Delete paper documents no professor references anymore.

    Identity repairs deliberately clear/replace professor.paper_ids. The old paper documents then
    become unreachable but still consume Atlas Free storage. This removes only unreachable papers.
    """
    d = st.db()
    used = set()
    for p in d.professors.find({}, {"paper_ids": 1}):
        used.update(p.get("paper_ids") or [])
    dead = []
    removed = 0
    for row in d.papers.find({}, {"_id": 1}):
        if row["_id"] not in used:
            dead.append(row["_id"])
        if len(dead) >= 1000:
            removed += d.papers.delete_many({"_id": {"$in": dead}}).deleted_count
            dead = []
    if dead:
        removed += d.papers.delete_many({"_id": {"$in": dead}}).deleted_count
    log(f"paper_orphans: removed {removed} unreferenced paper documents; {len(used)} referenced ids kept")


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


def upgrade_safe():
    """Apply the local/idempotent repairs for the current schema/identity upgrade.

    This does not call OpenAlex, Scholar, DDGS or an LLM itself. It only repairs/reopens MongoDB
    rows; the normal background pipeline then reprocesses them with the new rules.
    """
    for fn in (names, directory_profiles, retry_identity, orcid_conflicts, profile_rescan,
               author_duplicates, states, nonpersons, depts, benjaafar, paper_orphans):
        fn()
    counts()
    log("upgrade_safe: local data repairs complete; background processing can resume")


def counts():
    for inst in st.db().institutions.find({}, {"_id": 1}):
        st.recount(inst["_id"])
    st.invalidate_search()
    log("counts: every university recomputed from its professor rows")


STEPS = {"upgrade_safe": upgrade_safe, "names": names, "directory_profiles": directory_profiles,
         "retry_identity": retry_identity, "orcid_conflicts": orcid_conflicts, "profile_rescan": profile_rescan,
         "author_duplicates": author_duplicates, "states": states, "nonpersons": nonpersons,
         "depts": depts, "benjaafar": benjaafar, "hiring": hiring, "grants": grants,
         "grant_recheck": grant_recheck, "identity": identity, "papers": papers, "paper_orphans": paper_orphans,
         "affiliation": affiliation, "counts": counts}

if __name__ == "__main__":
    for name in (sys.argv[1:] or list(STEPS)):
        STEPS[name]()
    counts()
