"""Google Scholar fallback for professors OpenAlex could not resolve on name + institution.

  1. web search for the professor's Scholar profile ("<name>" <university> site:scholar.google.com)
  2. keep only a profile whose title is the professor's name AND whose search snippet names the
     university or shows a verified email on the university's domain
  3. read the profile's paper titles (through the reader; Scholar blocks direct requests)
  4. look up up to 5 of those titles in OpenAlex; accept the OpenAlex author on those works whose
     name matches the professor, if at least 2 distinct papers point to the same author
Anything less is UNRESOLVED and goes to Staff review. Nothing is guessed.
"""

import re
import urllib.parse

from services import fetchers as fx
from services import store as st

TITLE_RE = re.compile(r"\[([^\]]{12,300})\]\((https?://scholar\.google\.[^)]*view_op=view_citation[^)]*)\)")
MIN_AGREEING_PAPERS = 2


def _key(title):
    """Title comparison key: letters and digits only (Scholar and OpenAlex punctuate differently),
    first 120 chars (Scholar truncates long titles with an ellipsis)."""
    t = st.normalize_name(title).replace("\u2026", "")
    return re.sub(r"[^a-z0-9]", "", t)[:120]


def _names_ok(names_match, prof_name, cand):
    """names_match plus hyphenated surnames: "Elizabeth Bondi-Kelly" also matches "Elizabeth Bondi"
    (same first name, and the candidate's surname is one part of the hyphenated one)."""
    if names_match(prof_name, cand):
        return True
    a, b = prof_name.strip().split(), st.normalize_name(cand).split()
    if len(a) < 2 or len(b) < 2 or "-" not in a[-1]:
        return False
    parts = st.normalize_name(a[-1]).split()
    return st.normalize_name(a[0]) == b[0] and b[-1] in parts


def _user_id(url):
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (q.get("user") or [""])[0]


def _uni_words(name):
    """Distinctive words of the university name ("University of Michigan-Ann Arbor" -> michigan, ann, arbor)."""
    stop = {"university", "of", "the", "at", "and", "in", "main", "campus", "college", "institute", "state"}
    words = [w for w in st.normalize_name(name.replace("-", " ")).split() if w not in stop and len(w) > 2]
    return words or st.normalize_name(name).split()


def _affiliated(snippet, inst, domain):
    s = st.normalize_name(snippet)
    if domain and ("verified email at " + domain.lower()) in snippet.lower():
        return True
    if domain:
        m = re.search(r"verified email at ([a-z0-9.-]+)", snippet.lower())
        if m and (m.group(1) == domain or m.group(1).endswith("." + domain)):
            return True
    words = _uni_words(inst.get("name", ""))
    return all(w in s for w in words[:2])


def find_profile(p, inst, domain, names_match):
    """Returns (profile dict or None, status). status: FOUND / NONE / SEARCH_UNAVAILABLE."""
    from services import websearch as ws
    short = inst.get("name", "").split("-")[0]
    try:
        rows = ws.search(f'"{p["name"]}" {short} site:scholar.google.com', 8)
    except ws.SearchUnavailable:
        return None, "SEARCH_UNAVAILABLE"
    for r in rows:
        if "scholar.google." not in r["url"] or not _user_id(r["url"]):
            continue
        title_name = re.sub(r"\s*-\s*Google Scholar.*$", "", r.get("title") or "").strip()
        if not _names_ok(names_match, p["name"], title_name):
            continue
        if not _affiliated(r.get("snippet") or "", inst, domain):
            continue
        return {"user": _user_id(r["url"]), "url": f"https://scholar.google.com/citations?user={_user_id(r['url'])}&hl=en",
                "name": title_name, "snippet": (r.get("snippet") or "")[:300]}, "FOUND"
    return None, "NONE"


def paper_titles(profile_url, limit=8):
    page = fx.fetch_page(profile_url)
    if not page.get("ok"):
        return []
    seen, out = set(), []
    for m in TITLE_RE.finditer(page["text"]):
        t = m.group(1).strip()
        k = st.normalize_name(t)
        if k not in seen:
            seen.add(k)
            out.append(t)
        if len(out) >= limit:
            break
    return out


def match_via_scholar(p, inst, inst_oid, names_match):
    """Returns (fields to store, review_reason or ""). review_reason is set when it stays unresolved."""
    from services import discovery
    domain = discovery.domain_of(inst.get("official_website") or "")
    prof, status = find_profile(p, inst, domain, names_match)
    if status == "SEARCH_UNAVAILABLE":
        return {}, ""                          # web search cooling down: try again on the next run
    if prof is None:
        return {"scholar_checked": st.now_iso()}, "NO_SCHOLAR_PROFILE"
    titles = paper_titles(prof["url"])
    base = {"scholar_checked": st.now_iso(), "scholar_url": prof["url"], "scholar_id": prof["user"]}
    if not titles:
        return base, "SCHOLAR_PROFILE_UNREADABLE"
    votes, evidence = {}, {}
    for t in titles[:5]:
        target = _key(t)
        for w in fx.search_work_by_title(t):
            if _key(w.get("title") or "") != target:
                continue
            for au in w.get("authorships") or []:
                a = au.get("author") or {}
                aid = fx.short_id(a.get("id") or "")
                if aid and _names_ok(names_match, p["name"], a.get("display_name") or ""):
                    votes[aid] = votes.get(aid, 0) + 1
                    evidence.setdefault(aid, []).append(t)
            break
    if votes:
        best = max(votes, key=votes.get)
        tied = [a for a, v in votes.items() if v == votes[best]]
        if votes[best] >= MIN_AGREEING_PAPERS and len(tied) == 1:
            return dict(base, openalex_author_id=best, match_status="MATCHED", match_method="GOOGLE_SCHOLAR",
                        match_note=f"Matched via Google Scholar profile: {votes[best]} of its papers belong to this "
                                   f"OpenAlex author (e.g. \"{evidence[best][0][:90]}\")."), ""
        return dict(base, match_note=f"Google Scholar profile found, but its papers point to "
                                     f"{len(votes)} different OpenAlex authors or too few agree."), "SCHOLAR_AMBIGUOUS"
    return dict(base, match_note="Google Scholar profile found, but none of its papers were found in OpenAlex."), "SCHOLAR_PAPERS_NOT_IN_OPENALEX"
