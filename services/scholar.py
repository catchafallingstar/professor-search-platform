"""Google Scholar step of the identity chain (no paid API).

  1. FIND the profile:
       a. a Scholar profile URL already linked from the faculty page / personal page, or
       b. web search (DDGS, shared cooldown) for "<name>" <university> site:scholar.google.com.
          A co-author list on someone else's profile also counts as a lead: its link to the
          professor's own profile is followed.
  2. VERIFY it is this person: read the profile itself (rendered HTML via the reader, so Google's
     bot check is not hit directly) and require the professor's name AND this university in the
     profile affiliation or "Verified email at <university domain>".
  3. USE it:
       * its papers are looked up in OpenAlex; >= 2 agreeing -> MATCHED (OpenAlex author id)
       * otherwise the Scholar profile itself is the identity (match_status SCHOLAR): its papers,
         years and venues are stored as the professor's publications. Nothing is dropped just
         because OpenAlex lists an old affiliation.
Scholar requests are spaced by SCHOLAR_MIN_GAP seconds (default 20) under a process-wide lock, so
running and waiting jobs never hit Scholar at the same time.
"""

import html as htmllib
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from services import fetchers as fx
from services import store as st
from services import name_utils as nu

MIN_AGREEING_PAPERS = 2
TITLE_RE = re.compile(r"\[([^\]]{12,300})\]\((https?://scholar\.google\.[^)]*view_op=view_citation[^)]*)\)")
_LOCK = threading.Lock()
_LAST = {"t": 0.0}
GAP = lambda: float(os.environ.get("SCHOLAR_MIN_GAP", "20") or 20)


def _key(title):
    """Title comparison key: letters and digits only, first 120 chars."""
    t = st.normalize_name(title).replace("\u2026", "")
    return re.sub(r"[^a-z0-9]", "", t)[:120]


def _names_ok(names_match, prof_name, cand):
    """names_match plus hyphenated surnames ("Elizabeth Bondi-Kelly" ~ "Elizabeth Bondi")."""
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
    stop = {"university", "of", "the", "at", "and", "in", "main", "campus", "college", "institute", "state", "ann", "arbor"}
    words = [w for w in st.normalize_name(re.split(r"[-,]", name or "")[0]).split() if w not in stop and len(w) > 2]
    return words or st.normalize_name(name).split()


# ---------- reading a profile (rendered HTML through the reader, spaced) ----------

def _get_profile_html(user):
    url = f"https://scholar.google.com/citations?user={user}&hl=en&cstart=0&pagesize=100"
    with _LOCK:                                      # one Scholar request at a time, process-wide
        wait = GAP() - (time.time() - _LAST["t"])
        if wait > 0:
            time.sleep(wait)
        try:
            req = urllib.request.Request(fx.READER + url, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*",
                                                                   "X-Return-Format": "html", "X-Timeout": "30"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read(4_000_000).decode("utf-8", "ignore")
        except Exception as e:
            print(f"[scholar] profile {user} unreadable: {e}")
            return ""
        finally:
            _LAST["t"] = time.time()


def read_profile(user):
    """{"user","url","name","affiliation","email_domain","homepage","papers":[{title,authors,venue,year}]} or None."""
    h = _get_profile_html(user)
    m = re.search(r'id="gsc_prf_in">([^<]+)', h)
    if not m:
        return None
    aff = re.search(r'class="gsc_prf_il">(.*?)</div>', h, re.S)
    aff_text = htmllib.unescape(re.sub(r"<[^>]+>", " ", aff.group(1))).strip() if aff else ""
    em = re.search(r'id="gsc_prf_ivh">Verified email at ([a-z0-9.\-]+)', h)
    home = re.search(r'href="([^"]+)"[^>]*class="gsc_prf_ila"[^>]*>Homepage', h)
    papers = []
    for r in re.findall(r'<tr class="gsc_a_tr">(.*?)</tr>', h, re.S):
        t = re.search(r'class="gsc_a_at">([^<]*)', r)
        g = re.findall(r'<div class="gs_gray">([^<]*)', r)
        y = re.search(r'gsc_a_hc gs_ibl">(\d*)', r)
        if t and t.group(1).strip():
            papers.append({"title": htmllib.unescape(t.group(1)).strip(), "authors": htmllib.unescape(g[0]) if g else "",
                           "venue": htmllib.unescape(g[1]) if len(g) > 1 else "", "year": int(y.group(1)) if y and y.group(1) else 0})
    return {"user": user, "url": f"https://scholar.google.com/citations?user={user}&hl=en",
            "name": htmllib.unescape(m.group(1)).strip(), "affiliation": re.sub(r"\s+", " ", aff_text)[:200],
            "email_domain": em.group(1) if em else "", "homepage": home.group(1) if home else "", "papers": papers}


def _verified(prof, p, inst, domain, names_match):
    """The profile must carry this person's name AND this university (affiliation or verified email)."""
    if not prof or not _names_ok(names_match, p["name"], prof["name"]):
        return False
    d = prof.get("email_domain", "")
    if domain and d and (d == domain or d.endswith("." + domain)):
        return True
    aff = st.normalize_name(prof.get("affiliation", ""))
    return bool(aff) and all(w in aff for w in _uni_words(inst.get("name", ""))[:2])


# ---------- finding candidate profile ids ----------

def _ids_from_pages(p):
    """Scholar profile links on the faculty page (and its personal / lab page).

    enrich_profile() already scans the whole official page, so reuse scholar_id when present
    instead of fetching the page a second time.
    """
    out = []
    if p.get("scholar_id"):
        out.append(str(p["scholar_id"]))
    for url in [p.get("faculty_url"), p.get("personal_url"), p.get("lab_url")]:
        if not url:
            continue
        pg = fx.fetch_page_cached(url)
        for m in re.finditer(r"scholar\.google\.[a-z.]+/citations\?[^)\s\"']*user=([A-Za-z0-9_\-]{8,32})", pg.get("text") or ""):
            if m.group(1) not in out:
                out.append(m.group(1))
    return out


def _ids_from_search(p, inst):
    """DDGS results: the professor's own profile, or a co-author link to it on another profile."""
    from services import websearch as ws
    uni = re.split(r"[-,]", inst.get("name", ""))[0].strip()
    rows = ws.search(f'"{nu.clean_person_name(p["name"])}" {uni} site:scholar.google.com', 10)   # SearchUnavailable propagates
    last = st.normalize_name(p["name"]).split()[-1]
    own, via_coauthor = [], []
    for r in rows:
        uid = _user_id(r["url"])
        if "scholar.google." not in r["url"] or not uid:
            continue
        title = st.normalize_name(re.sub(r"\s*-\s*Google.*$", "", r.get("title") or ""))
        if last in title.split():
            own.append(uid)
        elif last in st.normalize_name(r.get("snippet") or ""):
            via_coauthor.append(uid)                 # the professor is listed as a co-author here
    return list(dict.fromkeys(own)), list(dict.fromkeys(via_coauthor))


def _coauthor_link(host_user, p, names_match):
    """Follow a co-author profile's link to the professor's own profile."""
    h = _get_profile_html(host_user)
    for uid, name in re.findall(r'href="/citations\?user=([A-Za-z0-9_\-]{12})[^"]*"[^>]*>([^<]+)</a>', h):
        if _names_ok(names_match, p["name"], htmllib.unescape(name)):
            return uid
    return ""


def find_linked_profile(p, inst, domain, names_match):
    """Only Scholar profiles explicitly linked by the official faculty/personal/lab page.

    Returns (profile, FOUND|NONE|UNREADABLE|NOT_VERIFIED).  A linked profile is stronger than
    a web-search guess and is tried before OpenAlex name matching.
    """
    ids = _ids_from_pages(p)
    if not ids:
        return None, "NONE"
    unreadable = False
    for uid in ids:
        prof = read_profile(uid)
        if prof is None:
            unreadable = True
            continue
        if _verified(prof, p, inst, domain, names_match):
            return prof, "FOUND"
    return None, ("UNREADABLE" if unreadable else "NOT_VERIFIED")


def find_profile(p, inst, domain, names_match):
    """Returns (verified profile dict or None, status FOUND / NONE / SEARCH_UNAVAILABLE)."""
    from services import websearch as ws
    tried = set()
    candidates = _ids_from_pages(p)
    try:
        own, coauthor_hosts = _ids_from_search(p, inst)
    except ws.SearchUnavailable:
        if not candidates:
            return None, "SEARCH_UNAVAILABLE"
        own, coauthor_hosts = [], []
    for uid in candidates + own:
        if uid in tried:
            continue
        tried.add(uid)
        prof = read_profile(uid)
        if _verified(prof, p, inst, domain, names_match):
            return prof, "FOUND"
    for host in coauthor_hosts[:2]:
        uid = _coauthor_link(host, p, names_match)
        if uid and uid not in tried:
            tried.add(uid)
            prof = read_profile(uid)
            if _verified(prof, p, inst, domain, names_match):
                return prof, "FOUND"
    return None, "NONE"


def paper_titles(profile_url, limit=8):
    prof = read_profile(_user_id(profile_url))
    return [x["title"] for x in (prof or {}).get("papers", [])[:limit]]


# ---------- the step ----------

def _fields_from_profile(p, inst, prof, names_match):
    """Convert one already-verified Scholar profile to Professor Atlas fields."""
    base = {"scholar_checked": st.now_iso(), "scholar_url": prof["url"], "scholar_id": prof["user"],
            "scholar_affiliation": prof["affiliation"], "personal_url": p.get("personal_url") or prof.get("homepage", "")}
    votes, evidence = {}, {}
    for x in prof["papers"][:6]:
        target = _key(x["title"])
        try:
            works = fx.search_work_by_title(x["title"])
        except fx.RateLimited:
            raise
        except Exception:
            works = []
        for w in works:
            if _key(w.get("title") or "") != target:
                continue
            for au in w.get("authorships") or []:
                a = au.get("author") or {}
                aid = fx.short_id(a.get("id") or "")
                if aid and _names_ok(names_match, p["name"], a.get("display_name") or ""):
                    votes[aid] = votes.get(aid, 0) + 1
                    evidence.setdefault(aid, []).append(x["title"])
            break
    if votes:
        best = max(votes, key=votes.get)
        if votes[best] >= MIN_AGREEING_PAPERS and list(votes.values()).count(votes[best]) == 1:
            return dict(base, openalex_author_id=best, match_status="MATCHED", match_method="GOOGLE_SCHOLAR",
                        match_note=f"Google Scholar profile verified ({prof['affiliation'] or prof['email_domain']}); "
                                   f"{votes[best]} of its papers belong to OpenAlex author {best} "
                                   f"(e.g. \"{evidence[best][0][:90]}\").")
    return dict(base, scholar_papers=prof["papers"], match_status="SCHOLAR", match_method="GOOGLE_SCHOLAR",
                match_note=f"Google Scholar profile verified ({prof['affiliation'] or 'verified email at ' + prof['email_domain']}); "
                           f"{len(prof['papers'])} publications taken from it.")


def match_via_linked_scholar(p, inst, names_match):
    """Try only Scholar links supplied by the professor's own official pages.

    If such a link exists but cannot be read now, return RETRY_LINKED instead of silently
    falling through to a weaker identity source.
    """
    from services import discovery
    domain = discovery.domain_of(inst.get("official_website") or "")
    prof, status = find_linked_profile(p, inst, domain, names_match)
    if status == "FOUND":
        return _fields_from_profile(p, inst, prof, names_match), ""
    if status == "UNREADABLE":
        return {}, "RETRY_LINKED"
    if status == "NOT_VERIFIED":
        return {"scholar_checked": st.now_iso()}, "LINKED_NOT_VERIFIED"
    return {}, "NO_LINK"


def match_via_scholar(p, inst, inst_oid, names_match):
    """Returns (fields to store, reason if no identity). A verified Scholar profile is an identity
    on its own: if OpenAlex agrees we store the OpenAlex author, otherwise the Scholar papers."""
    from services import discovery
    domain = discovery.domain_of(inst.get("official_website") or "")
    prof, status = find_profile(p, inst, domain, names_match)
    if status == "SEARCH_UNAVAILABLE":
        return {}, ""                                # web search cooling down: retried later
    if prof is None:
        return {"scholar_checked": st.now_iso()}, "NO_SCHOLAR_PROFILE"
    return _fields_from_profile(p, inst, prof, names_match), ""
