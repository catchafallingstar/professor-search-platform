"""Federal grant search by investigator name (no API key needed):

  * NSF Award Search API     https://api.nsf.gov/services/v1/awards.json  (PI and co-PI)
  * NIH RePORTER API v2      https://api.reporter.nih.gov/v2/projects/search  (PIs)

Kept only when the investigator's name matches the professor AND the awardee organization is
the professor's university, and the award started within the last `years` years.
NIH lists one record per fiscal year; they are merged per core project number.
"""

import json
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "ProfessorAtlas/1.0 (research directory)", "Accept": "application/json"}


def _get(url, body=None, timeout=40):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=dict(UA, **({"Content-Type": "application/json"} if body is not None else {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _norm(s):
    return re.sub(r"[^a-z ]", " ", (s or "").lower()).split()


def _uni_words(uni):
    stop = {"university", "of", "the", "at", "and", "in", "main", "campus", "college", "regents", "trustees", "ann", "arbor"}
    words = [w for w in _norm(re.split(r"\s*[-,]\s*", uni or "")[0]) if w not in stop and len(w) > 2]
    return words or _norm(uni)


def _org_ok(org, uni):
    o = " ".join(_norm(org))
    return all(w in o for w in _uni_words(uni))


def _nsf_date(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else ""


def nsf_awards(first, last, uni, years=10, names_match=None):
    since = time.gmtime().tm_year - years
    fields = "id,title,piFirstName,piLastName,coPDPI,awardeeName,startDate,expDate,fundsObligatedAmt,agency"
    out = []
    for param in ("pdPIName", "coPDPI"):
        q = urllib.parse.urlencode({param: f"{first} {last}", "printFields": fields, "rpp": 25,
                                    "startDateStart": f"01/01/{since}"})
        try:
            data = _get("https://api.nsf.gov/services/v1/awards.json?" + q)
        except Exception:
            continue
        for a in (data.get("response") or {}).get("award") or []:
            pi = f"{a.get('piFirstName', '')} {a.get('piLastName', '')}"
            co = a.get("coPDPI") or []
            co = co if isinstance(co, list) else [co]
            role = ""
            if names_match(f"{first} {last}", pi):
                role = "PI"
            elif any(names_match(f"{first} {last}", re.sub(r"~\d+$", "", c)) for c in co):
                role = "CO_PI"
            if not role or not _org_ok(a.get("awardeeName", ""), uni):
                continue
            out.append({"key": "NSF-" + a["id"], "title": a.get("title", ""), "funder_name": "National Science Foundation",
                        "funder_award_id": a["id"], "role": role, "amount": float(a.get("fundsObligatedAmt") or 0),
                        "currency": "USD", "start_date": _nsf_date(a.get("startDate")), "end_date": _nsf_date(a.get("expDate")),
                        "url": f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={a['id']}", "source": "NSF"})
    return out


def nih_awards(first, last, uni, years=10, names_match=None):
    since = time.gmtime().tm_year - years
    # every fiscal year since the award could have started, so multi-year totals are complete
    body = {"criteria": {"pi_names": [{"first_name": first, "last_name": last}],
                         "fiscal_years": list(range(since - 5, time.gmtime().tm_year + 2))},
            "include_fields": ["ProjectTitle", "ProjectNum", "CoreProjectNum", "FiscalYear", "AwardAmount", "Organization",
                               "PrincipalInvestigators", "ProjectStartDate", "ProjectEndDate", "AgencyIcAdmin", "ProjectDetailUrl"],
            "limit": 500}
    try:
        data = _get("https://api.reporter.nih.gov/v2/projects/search", body)
    except Exception:
        return []
    merged = {}
    for r in data.get("results") or []:
        if not _org_ok((r.get("organization") or {}).get("org_name", ""), uni):
            continue
        pis = r.get("principal_investigators") or []
        me = [p for p in pis if names_match(f"{first} {last}", p.get("full_name") or "")]
        if not me:
            continue
        # one project can carry several activity codes (e.g. T90 + R90 for the same program) and
        # one record per fiscal year: group by title + start date, keep the first project number
        group = ((r.get("project_title") or "").lower().strip(), (r.get("project_start_date") or "")[:10])
        core = r.get("core_project_num") or r.get("project_num")
        m = merged.get(group)
        amt = float(r.get("award_amount") or 0)
        if m is None:
            merged[group] = {"key": "NIH-" + core, "title": r.get("project_title", ""), "funder_name": "National Institutes of Health",
                            "funder_award_id": core, "role": "PI" if me[0].get("is_contact_pi") or len(pis) == 1 else "MULTI_PI",
                            "amount": amt, "currency": "USD", "start_date": (r.get("project_start_date") or "")[:10],
                            "end_date": (r.get("project_end_date") or "")[:10],
                            "url": f"https://reporter.nih.gov/project-details/{r.get('project_num', core)}", "source": "NIH"}
        else:
            m["amount"] += amt                       # one record per fiscal year: total them
            m["end_date"] = max(m["end_date"], (r.get("project_end_date") or "")[:10])
    return [g for g in merged.values() if (g["start_date"][:4] or "0") >= str(since)]


def search(name, uni, names_match, years=10):
    """All federal awards for this person at this university that started in the last `years` years."""
    parts = (name or "").replace(".", " ").split()
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    return nsf_awards(first, last, uni, years, names_match) + nih_awards(first, last, uni, years, names_match)
