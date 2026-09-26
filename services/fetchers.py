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


def _worker_loop():
    base = None
    while WORKER["running"]:
        if rate_limit_status()["limited"]:
            time.sleep(30)
            continue
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
                WORKER["running"] = False
                break
        except Exception as e:
            print(f"[worker] step failed: {e}")
            base = None
            time.sleep(10)
        time.sleep(1)


def start_worker():
    WORKER["running"] = True
    t = WORKER.get("thread")
    if t is None or not t.is_alive():
        WORKER["thread"] = threading.Thread(target=_worker_loop, daemon=True)
        WORKER["thread"].start()


def stop_worker():
    WORKER["running"] = False


def worker_running():
    return WORKER["running"]


def llm_configured():
    """True when an LLM key is available (needed for faculty-page extraction and hiring research)."""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def config_status():
    try:
        ok = _get_json("/institutions", {"per-page": "1"}) is not None
    except RateLimited:
        ok = False
    return {
        "openalex_ok": ok,
        "openalex_mailto": bool(os.environ.get("OPENALEX_MAILTO")),
        "openalex_key": bool(os.environ.get("OPENALEX_API_KEY")),
        "llm_key": llm_configured(),
    }


class RateLimited(Exception):
    """OpenAlex daily budget exhausted. The worker pauses instead of recording false 'unresolved' results."""


RATE_LIMIT = {"until": 0.0, "message": ""}


def rate_limit_status():
    left = int(RATE_LIMIT["until"] - time.time())
    return {"limited": left > 0, "seconds_left": max(left, 0), "message": RATE_LIMIT["message"]}


def _get_json(path, params=None):
    if RATE_LIMIT["until"] > time.time():
        raise RateLimited(RATE_LIMIT["message"])
    q = dict(params or {})
    mail = os.environ.get("OPENALEX_MAILTO", "")
    if mail:
        q["mailto"] = mail
    key = os.environ.get("OPENALEX_API_KEY", "")  # optional: premium key, leave empty otherwise
    if key:
        q["api_key"] = key
    url = OPENALEX + path + ("?" + urllib.parse.urlencode(q) if q else "")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "0") or 0)
                if wait > 60:  # daily budget gone, not a short burst limit
                    RATE_LIMIT["until"] = time.time() + wait
                    RATE_LIMIT["message"] = (f"OpenAlex rate limit reached; resets in about {wait // 60} min. "
                                             "Add OPENALEX_API_KEY (free at openalex.org) for a much higher daily limit.")
                    raise RateLimited(RATE_LIMIT["message"])
                time.sleep(max(wait, 2 * (attempt + 1)))
                continue
            print(f"[openalex] {url} -> HTTP {e.code}")
            return None
        except RateLimited:
            raise
        except Exception as e:  # network errors
            print(f"[openalex] {url} failed: {e}")
            time.sleep(1)
    return None


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
