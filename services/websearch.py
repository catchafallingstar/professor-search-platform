"""Web search for candidate URLs (DDGS: DuckDuckGo / Bing / others, no API key).

Search results are only CANDIDATES. The pipeline still fetches every page itself, checks
the domain, extracts text and verifies the quote character-by-character.

Anti-bot / politeness behaviour:
  * one search at a time (process-wide lock)
  * minimum gap between searches (SEARCH_MIN_GAP, default 6 s) + random jitter (0-4 s)
  * results cached for 30 days (MongoDB "search_cache"), so maintenance runs don't re-search
  * on a rate limit: exponential cooldown 2 -> 4 -> 8 ... up to 60 min (state kept in MongoDB)
  * circuit breaker: after 5 consecutive failures the search is "tripped" for the cooldown;
    callers get status RATE_LIMITED and the professor is re-checked later (never "no signal")
  * a daily cap (SEARCH_DAILY_CAP, default 400 queries) so the pipeline stays a polite guest
"""

import os
import random
import threading
import time
import urllib.parse

from services import store as st

_LOCK = threading.Lock()
_STATE = {"last": 0.0}
MIN_GAP = lambda: float(os.environ.get("SEARCH_MIN_GAP", "6") or 6)
DAILY_CAP = lambda: int(os.environ.get("SEARCH_DAILY_CAP", "400") or 400)
CACHE_DAYS = 30

HIGH = ("/openings", "/join", "/join-us", "/positions", "/opportunities", "/prospective", "/students", "/lab", "/people", "/research", "/group", "/team")
LOW = ("/news", "/events", "/alumni", "/admissions", "/campus-life", "/athletics", "/giving", "/calendar")
BLOCKED_HOSTS = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com",
                 "ratemyprofessors.com", "glassdoor.com", "indeed.com", "researchgate.net", "academia.edu",
                 "wikipedia.org", "zhihu.com", "reddit.com")


class SearchUnavailable(Exception):
    """Search is cooling down / tripped / over the daily cap. Retry later; not 'no results'."""


def _state():
    return st.get_setting("search_state", {}) or {}


def _save(s):
    st.set_setting("search_state", s)


def status():
    s = _state()
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    used = s.get("used", 0) if s.get("day") == day else 0
    cool = max(0, int(s.get("cooldown_until", 0) - now))
    return {"available": cool == 0 and used < DAILY_CAP(), "cooldown_seconds": cool, "used_today": used,
            "daily_cap": DAILY_CAP(), "consecutive_failures": s.get("fails", 0), "last_error": s.get("last_error", "")}


def _fail(err):
    s = _state()
    fails = int(s.get("fails", 0)) + 1
    minutes = min(60, 2 ** min(fails, 6))                 # 2,4,8,16,32,60
    s.update(fails=fails, last_error=str(err)[:200])
    if fails >= 5 or "ratelimit" in type(err).__name__.lower() or "202" in str(err) or "429" in str(err):
        s["cooldown_until"] = time.time() + minutes * 60
    _save(s)


def _ok():
    s = _state()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if s.get("day") != day:
        s.update(day=day, used=0)
    s.update(fails=0, used=int(s.get("used", 0)) + 1, last_error="")
    _save(s)


def _cached(q):
    d = st.db().search_cache.find_one({"_id": q})
    if d and time.time() - d.get("t", 0) < CACHE_DAYS * 86400:
        return d["results"]
    return None


def search(query, max_results=8):
    """Returns [{"url", "title", "snippet"}]. Raises SearchUnavailable when cooling down."""
    hit = _cached(query)
    if hit is not None:
        return hit
    stat = status()
    if not stat["available"]:
        raise SearchUnavailable(f"search paused ({stat['cooldown_seconds']}s cooldown, {stat['used_today']}/{stat['daily_cap']} today)")
    from ddgs import DDGS
    with _LOCK:
        wait = MIN_GAP() + random.uniform(0, 4) - (time.time() - _STATE["last"])
        if wait > 0:
            time.sleep(wait)
        try:
            rows = DDGS(timeout=20).text(query, max_results=max_results, region="us-en", safesearch="moderate") or []
        except Exception as e:
            _STATE["last"] = time.time()
            if "no results" in str(e).lower():
                # DDGS raises when a query simply has no hits (common for "site:" queries).
                # That is an answer, not a failure: it must not pause search for everyone.
                rows = []
            else:
                _fail(e)
                raise SearchUnavailable(f"{type(e).__name__}: {str(e)[:150]}")
        _STATE["last"] = time.time()
    _ok()
    out = [{"url": r.get("href") or r.get("url") or "", "title": r.get("title") or "", "snippet": r.get("body") or ""}
           for r in rows if (r.get("href") or r.get("url"))]
    st.db().search_cache.replace_one({"_id": query}, {"_id": query, "t": time.time(), "results": out}, upsert=True)
    return out


def _host(url):
    return urllib.parse.urlparse(url or "").netloc.lower()


def rank(results, domain, name):
    """Deterministic ranking of candidate hiring pages. Off-domain hosts are allowed (lab sites
    often live elsewhere, e.g. github.io) but ranked lower; social/job/aggregator sites dropped."""
    last = (name.split() or [""])[-1].lower()
    scored = []
    for r in results:
        url = r["url"]
        host = _host(url)
        if not url.startswith("http") or any(b in host for b in BLOCKED_HOSTS) or url.lower().endswith((".pdf", ".doc", ".docx")):
            continue
        path = urllib.parse.urlparse(url).path.lower()
        s = 0
        if domain and (host == domain or host.endswith("." + domain)):
            s += 50
        if any(h in path for h in HIGH):
            s += 30
        if any(l in path for l in LOW):
            s -= 40
        text = (r.get("title", "") + " " + r.get("snippet", "")).lower()
        if last and last in text:
            s += 20
        if any(w in text for w in ("recruit", "openings", "prospective", "join", "phd student", "postdoc", "position")):
            s += 15
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    seen, out = set(), []
    for s, r in scored:
        if r["url"] not in seen and s > -20:
            seen.add(r["url"])
            out.append(r)
    return out


def hiring_queries(name, domain):
    q = [f'"{name}" "PhD students"', f'"{name}" recruiting', f'"{name}" "join our lab"', f'"{name}" openings']
    if domain:
        q = [f'site:{domain} "{name}" PhD', f'site:{domain} "{name}" recruiting'] + q
    return q


def find_hiring_candidates(name, domain, max_queries=3, max_urls=5):
    """Run a few controlled queries (stop early once enough candidates) -> ranked URLs."""
    found = []
    for q in hiring_queries(name, domain)[:max_queries]:
        found += search(q)
        if len(rank(found, domain, name)) >= max_urls:
            break
    return [r["url"] for r in rank(found, domain, name)[:max_urls]]
