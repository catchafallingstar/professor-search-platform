"""Plain-Python HTTP helpers: OpenAlex REST client and official-page fetching.

No API key is required for OpenAlex. Setting OPENALEX_MAILTO (Settings > Environment)
puts requests in OpenAlex's faster "polite pool".
"""

import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

OPENALEX = "https://api.openalex.org"
UA = "Mozilla/5.0 (ProfessorAtlas research crawler)"


import threading

WORK_LOCK = threading.Lock()


def acquire():
    WORK_LOCK.acquire()


def release():
    WORK_LOCK.release()


# ---------------- background worker ----------------
# Each step is sent to the app's own /function/process_next_step endpoint so it runs
# inside a normal request context (graph writes persist and permissions apply).

WORKER = {"running": False, "thread": None}


def _local_api():
    for port in (os.environ.get("JAC_API_PORT"), "8001", "8000"):
        if not port:
            continue
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3)
            return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None


_RUN_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".worker_running")


def _worker_loop():
    base = None
    time.sleep(5)
    while WORKER["running"]:
        base = base or _local_api()
        if not base:
            time.sleep(5)
            continue
        try:
            req = urllib.request.Request(base + "/function/process_next_step", data=b"{}",
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            result = ((body or {}).get("data") or {}).get("result") or {}
            if result.get("all_done"):
                stop_worker()
                break
            if not result.get("ok", True) and rate_limit_status()["limited"]:
                # Nothing left to crawl and every key is cooling down: poll slowly.
                # A key added from the Pipeline page is picked up on the next poll.
                time.sleep(120)
        except Exception as e:
            print(f"[worker] step failed: {e}")
            base = None
            time.sleep(10)
        time.sleep(1)


def start_worker():
    WORKER["running"] = True
    open(_RUN_FLAG, "w").close()  # remembered across preview restarts
    t = WORKER.get("thread")
    if t is None or not t.is_alive():
        WORKER["thread"] = threading.Thread(target=_worker_loop, daemon=True)
        WORKER["thread"].start()


def stop_worker():
    WORKER["running"] = False
    try:
        os.remove(_RUN_FLAG)
    except OSError:
        pass


def resume_if_flagged():
    if os.path.exists(_RUN_FLAG) and not WORKER["running"]:
        start_worker()


def worker_running():
    return WORKER["running"]


def llm_configured():
    """True when an LLM key is available (needed for faculty-page extraction and hiring research)."""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


class RateLimited(Exception):
    """Every OpenAlex credential is exhausted. The worker pauses instead of recording false 'unresolved' results."""


# ---------------- OpenAlex credential pool ----------------
# Keys come from Settings > Environment:
#   OPENALEX_API_KEYS = "key1,key2,key3"   (comma/space/newline separated)
#   OPENALEX_API_KEY, OPENALEX_API_KEY_1 .. OPENALEX_API_KEY_20   (also accepted)
# The keyless anonymous pool is always the last fallback. When one credential hits its
# daily limit it cools down for the Retry-After period and the next one is used.

_ANON = ""
_COOLDOWN = {}      # credential -> unix time it becomes usable again
_BAD_KEYS = set()   # keys OpenAlex rejected as invalid


_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".openalex_keys")


def _file_keys():
    try:
        with open(_KEY_FILE) as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def add_key(key):
    """Add an OpenAlex key at runtime (no restart). Validates it with one live call first."""
    key = (key or "").strip()
    if not key or re.search(r"\s", key):
        return {"ok": False, "message": "Enter a single OpenAlex API key."}
    if key in _configured_keys():
        _COOLDOWN.pop(key, None)
        return {"ok": True, "message": "Key already in the pool."}
    try:
        req = urllib.request.Request(OPENALEX + "/institutions?per-page=1&api_key=" + urllib.parse.quote(key),
                                     headers={"User-Agent": UA})
        urllib.request.urlopen(req, timeout=20).read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False, "message": "OpenAlex rejected this key."}
        if e.code == 429:
            return {"ok": False, "message": "This key is also rate-limited right now."}
        return {"ok": False, "message": f"Could not verify key (HTTP {e.code})."}
    except Exception as e:
        return {"ok": False, "message": f"Could not verify key: {e}"}
    with open(_KEY_FILE, "a") as fh:
        fh.write(key + "\n")
    _BAD_KEYS.discard(key)
    return {"ok": True, "message": f"Key ...{key[-4:]} added. Processing continues with it now."}


def _configured_keys():
    keys = list(_file_keys())
    raw = os.environ.get("OPENALEX_API_KEYS", "")
    keys += [k for k in re.split(r"[\s,;]+", raw) if k]
    for name in ["OPENALEX_API_KEY"] + [f"OPENALEX_API_KEY_{i}" for i in range(1, 21)]:
        v = os.environ.get(name, "").strip()
        if v:
            keys.append(v)
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _credentials():
    return [k for k in _configured_keys() if k not in _BAD_KEYS] + [_ANON]


def _available():
    now = time.time()
    return [c for c in _credentials() if _COOLDOWN.get(c, 0) <= now]


def _mask(c):
    return "no-key pool" if c == _ANON else f"key ...{c[-4:]}"


_NOTICE = {"shown": False}


def first_rate_notice():
    """True only the first time per rate-limit wait (process-wide, survives across requests)."""
    if _NOTICE["shown"]:
        return False
    _NOTICE["shown"] = True
    return True


def clear_rate_notice():
    _NOTICE["shown"] = False


def rate_limit_status():
    creds = _credentials()
    avail = _available()
    if avail:
        return {"limited": False, "seconds_left": 0, "message": "",
                "keys_total": len(creds) - 1, "keys_available": len(avail)}
    left = int(min(_COOLDOWN.get(c, 0) for c in creds) - time.time())
    n = len(creds) - 1
    msg = (f"All OpenAlex credentials are rate-limited ({n} API key{'s' if n != 1 else ''} + no-key pool); "
           f"the next one frees up in about {max(left, 0) // 60} min. "
           "Add more keys to OPENALEX_API_KEYS (comma-separated) in Settings > Environment.")
    return {"limited": True, "seconds_left": max(left, 0), "message": msg,
            "keys_total": n, "keys_available": 0}


def config_status():
    try:
        ok = _get_json("/institutions", {"per-page": "1"}) is not None
    except RateLimited:
        ok = False
    rl = rate_limit_status()
    return {
        "openalex_ok": ok,
        "openalex_mailto": bool(os.environ.get("OPENALEX_MAILTO")),
        "openalex_key": len(_configured_keys()) > 0,
        "openalex_keys_total": len(_configured_keys()),
        "openalex_keys_available": rl["keys_available"],
        "openalex_keys_invalid": len(_BAD_KEYS),
        "llm_key": llm_configured(),
    }


def _get_json(path, params=None):
    mail = os.environ.get("OPENALEX_MAILTO", "")
    tried = 0
    while True:
        avail = _available()
        if not avail:
            raise RateLimited(rate_limit_status()["message"])
        cred = avail[0]  # keys first, anonymous pool last
        q = dict(params or {})
        if mail:
            q["mailto"] = mail
        if cred:
            q["api_key"] = cred
        url = OPENALEX + path + ("?" + urllib.parse.urlencode(q) if q else "")
        switch = False
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (401, 403) and cred:
                    print(f"[openalex] {_mask(cred)} rejected (HTTP {e.code}); dropping it")
                    _BAD_KEYS.add(cred)
                    switch = True
                    break
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After", "0") or 0)
                    if wait > 60:  # this credential's daily budget is gone -> rotate
                        _COOLDOWN[cred] = time.time() + wait
                        switch = True
                        break
                    time.sleep(max(wait, 2 * (attempt + 1)))
                    continue
                print(f"[openalex] {path} -> HTTP {e.code}")
                return None
            except Exception as e:  # network errors
                print(f"[openalex] {path} failed: {e}")
                time.sleep(1)
        if not switch:
            return None
        tried += 1
        if tried > len(_credentials()) + 1:
            raise RateLimited(rate_limit_status()["message"])


def short_id(full):
    return (full or "").rstrip("/").split("/")[-1]


def clean_doi(doi):
    d = (doi or "").strip()
    for p in ("https://doi.org/", "http://doi.org/", "doi.org/", "doi:"):
        if d.lower().startswith(p):
            d = d[len(p):]
    return d.lower()


def _results(data):
    return (data or {}).get("results") or []


def fetch_work_by_doi(doi):
    d = clean_doi(doi)
    return _get_json("/works/doi:" + d) if d else None


def search_work_by_title(title):
    if not (title or "").strip():
        return []
    return _results(_get_json("/works", {"search": title, "per-page": "5"}))


def search_authors(name, institution_id):
    params = {"search": name, "per-page": "10"}
    if institution_id:
        params["filter"] = "last_known_institutions.id:" + short_id(institution_id)
    return _results(_get_json("/authors", params))


def fetch_author(author_id):
    return _get_json("/authors/" + short_id(author_id))


def fetch_author_works(author_id, from_year):
    return _results(_get_json("/works", {
        "filter": f"authorships.author.id:{short_id(author_id)},from_publication_date:{from_year}-01-01",
        "sort": "publication_date:desc",
        "per-page": "50",
    }))


def fetch_award(award_id):
    return _get_json("/awards/" + short_id(award_id)) if award_id else None


def search_institution(name, ror_id):
    if ror_id:
        found = _get_json("/institutions/ror:" + short_id(ror_id))
        if found:
            return found
    res = _results(_get_json("/institutions", {"search": name, "filter": "country_code:US", "per-page": "1"}))
    return res[0] if res else None


def list_top_us_institutions(page):
    return _results(_get_json("/institutions", {
        "filter": "country_code:US,type:education",
        "sort": "works_count:desc", "per-page": "50", "page": str(page),
    }))


def fetch_institution_awards(institution_id):
    if not institution_id:
        return []
    return _results(_get_json("/awards", {
        # verified against the live API: awards are filtered by the awarded institution
        "filter": "institution_awarded.id:" + short_id(institution_id) + ",start_year:>" + str(time.gmtime().tm_year - 6),
        "sort": "start_year:desc",
        "per-page": "100",
    }))


# ---------------- official page fetching ----------------

def html_to_text(raw, base):
    s = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)

    def _link(m):
        href, label = m.group(1), " ".join(re.sub(r"(?s)<[^>]+>", " ", m.group(2)).split())
        if not label or href.startswith(("mailto:", "#", "javascript:")):
            return " " + label + " "
        return f" [{label}]({urllib.parse.urljoin(base, href)}) "

    s = re.sub(r"(?is)<a\s[^>]*?href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", _link, s)
    s = re.sub(r"(?i)<(br|/p|/div|/li|/tr|/h[1-6])[^>]*>", "\n", s)
    s = html.unescape(re.sub(r"(?s)<[^>]+>", " ", s))
    s = re.sub(r"[ \t\r]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def fetch_page(url):
    """Returns {"url", "title", "text", "ok"}; never raises."""
    out = {"url": url or "", "title": "", "text": "", "ok": False}
    if not url or not url.startswith("http"):
        return out
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            if "html" not in resp.headers.get("Content-Type", "") and "text" not in resp.headers.get("Content-Type", ""):
                return out
            raw = resp.read(2_000_000).decode("utf-8", errors="ignore")
            final = resp.geturl()
        m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        text = html_to_text(raw, final)
        out.update(url=final, title=html.unescape(" ".join(m.group(1).split()))[:200] if m else "",
                   text=text, ok=len(text) > 50)
    except Exception as e:
        print(f"[fetch] {url} failed: {e}")
    return out


# ---------------- rule-based faculty extraction (no LLM needed) ----------------

_TITLE_RE = re.compile(r"\b((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor\b[^\n\[\]]{0,80})", re.I)
_EXCLUDE_TITLE = re.compile(r"emerit|adjunct|affiliate|courtesy|visiting|lecturer|teaching|clinical|practice|research professor|professor of practice", re.I)
_LINK_RE = re.compile(r"\[([^\]]{3,160})\]\((https?://[^)\s]+)\)")
_BAD_NAME = re.compile(r"faculty|directory|people|department|school|college|university|research|news|events|about|contact|staff|students|home|program|center|lab\b|search|filter|view|profile|more|apply|give|login", re.I)


def _clean_name(s):
    s = re.sub(r"\s+", " ", s).strip(" ,;:-|")
    s = re.sub(r",?\s*(Ph\.?D\.?|PhD|P\.E\.|Jr\.?|Sr\.?|III|II)$", "", s).strip(" ,")
    return s


def _looks_like_name(s):
    if not s or _BAD_NAME.search(s) or re.search(r"\d|@|/", s):
        return False
    words = s.replace(",", " ").split()
    if not (2 <= len(words) <= 5):
        return False
    return all(w[0].isupper() or w.lower() in ("de", "van", "von", "da", "del", "la", "le", "di") for w in words if w)


def _title_near(text, start, end):
    """Title right after the link, or inside the link label."""
    after = text[end:end + 220]
    m = _TITLE_RE.search(after.split("[")[0])
    return m.group(1).strip() if m else ""


def extract_faculty_rules(page, department):
    """Pull professor-rank faculty from an official directory page: linked names + nearby titles."""
    if not page.get("ok"):
        return []
    text = page["text"]
    out, seen = [], set()
    for m in _LINK_RE.finditer(text):
        label, url = m.group(1).strip(), m.group(2)
        title = ""
        name = label
        inner = _TITLE_RE.search(label)
        if inner:  # "[Kira Barton Professor](url)"
            name = label[:inner.start()]
            title = inner.group(1)
        else:      # "[Aliaga](url)\n Professor"
            title = _title_near(text, m.start(), m.end())
        name = _clean_name(name)
        title = re.sub(r"\s+", " ", title).strip(" ,;")
        if not title or _EXCLUDE_TITLE.search(title) or not _looks_like_name(name):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        # Keep the rank only (drop trailing campus names etc.)
        rank = re.match(r"((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor)", title, re.I)
        out.append({"name": name, "title": (rank.group(1) if rank else title)[:80],
                    "department": department, "profile_url": url})
    return out


def squash(t):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split())


def same_domain(url, website):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    base = urllib.parse.urlparse(website or "").netloc.lower().replace("www.", "")
    parts = base.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else base
    return bool(root) and host.endswith(root)
