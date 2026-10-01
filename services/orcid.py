"""ORCID fallback for professors that neither OpenAlex (name + institution) nor Google Scholar resolved.

  1. ORCID public search: given + family name AND the university as an affiliation
  2. accept only ONE record whose name matches the professor and whose affiliation list names the
     university (no record or several -> nothing, never a guess)
  3. read that ORCID record's works (DOIs) and look them up in OpenAlex
  4. MATCHED when DOI-confirmed works point to one OpenAlex author whose name matches
The ORCID iD is kept even when no OpenAlex author is found (it is a verified identity).
"""

import json
import re
import urllib.parse
import urllib.request

from services import fetchers as fx
from services import store as st

API = "https://pub.orcid.org/v3.0"
HEAD = {"Accept": "application/json", "User-Agent": "ProfessorAtlas/1.0 (research directory)"}


def _get(url):
    req = urllib.request.Request(url, headers=HEAD)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _uni_core(name):
    """'University of Michigan-Ann Arbor' -> 'University of Michigan' (the form ORCID records use)."""
    return re.split(r"\s*[-,]\s*", name or "")[0].strip()


def find_record(p, inst, names_match):
    """Returns (orcid_id, record) or ("", None)."""
    parts = (p.get("name") or "").split()
    if len(parts) < 2:
        return "", None
    given, family = parts[0], parts[-1]
    uni = _uni_core(inst.get("name", ""))
    q = f'given-names:{given} AND family-name:{family} AND affiliation-org-name:"{uni}"'
    data = _get(f"{API}/expanded-search/?q={urllib.parse.quote(q)}&rows=10")
    hits = []
    for r in data.get("expanded-result") or []:
        full = f"{r.get('given-names') or ''} {r.get('family-names') or ''}".strip()
        insts = " | ".join(r.get("institution-name") or [])
        if names_match(p["name"], full) and st.normalize_name(uni) in st.normalize_name(insts):
            hits.append(r)
    if len(hits) != 1:
        return "", None
    return hits[0].get("orcid-id", ""), hits[0]


def work_dois(orcid_id, limit=10):
    data = _get(f"{API}/{orcid_id}/works")
    dois = []
    for g in data.get("group") or []:
        for eid in ((g.get("external-ids") or {}).get("external-id") or []):
            if (eid.get("external-id-type") or "").lower() == "doi" and eid.get("external-id-value"):
                d = fx.clean_doi(eid["external-id-value"])
                if d and d not in dois:
                    dois.append(d)
                break
        if len(dois) >= limit:
            break
    return dois


def match_via_orcid(p, inst, names_match, name_ok=None):
    """Returns (fields to store, reason when still unresolved or "")."""
    ok = name_ok or names_match
    try:
        oid, rec = find_record(p, inst, names_match)
    except Exception as e:
        return {}, ""                                   # ORCID unreachable now: try again next run
    if not oid:
        return {"orcid_checked": st.now_iso()}, "NO_ORCID_RECORD"
    base = {"orcid_checked": st.now_iso(), "orcid": oid}
    try:
        dois = work_dois(oid)
    except Exception:
        return base, "ORCID_WORKS_UNREADABLE"
    if not dois:
        return dict(base, match_note=f"ORCID record {oid} found, but it lists no works with a DOI."), "ORCID_NO_WORKS"
    votes = {}
    for d in dois[:6]:
        w = fx.fetch_work_by_doi(d)
        for au in (w or {}).get("authorships") or []:
            a = au.get("author") or {}
            aid = fx.short_id(a.get("id") or "")
            orc = fx.short_id(a.get("orcid") or "")
            if aid and (orc == oid or ok(p["name"], a.get("display_name") or "")):
                votes[aid] = votes.get(aid, 0) + (2 if orc == oid else 1)
                break
    if votes:
        best = max(votes, key=votes.get)
        if list(votes.values()).count(votes[best]) == 1:
            return dict(base, openalex_author_id=best, match_status="MATCHED", match_method="ORCID",
                        match_note=f"Matched via ORCID {oid} (name + {_uni_core(inst.get('name', ''))} affiliation); "
                                   f"its DOIs point to this OpenAlex author."), ""
    return dict(base, match_note=f"ORCID record {oid} found, but its works do not point to one OpenAlex author."), "ORCID_NOT_IN_OPENALEX"
