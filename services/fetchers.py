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


def llm_configured():
    """True when an LLM key is available (needed for faculty-page extraction and hiring research)."""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def config_status():
    return {
        "openalex_ok": _get_json("/institutions", {"per-page": "1"}) is not None,
        "openalex_mailto": bool(os.environ.get("OPENALEX_MAILTO")),
        "openalex_key": bool(os.environ.get("OPENALEX_API_KEY")),
        "llm_key": llm_configured(),
    }


def _get_json(path, params=None):
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
                time.sleep(2 * (attempt + 1))
                continue
            print(f"[openalex] {url} -> HTTP {e.code}")
            return None
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


def squash(t):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split())


def same_domain(url, website):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    base = urllib.parse.urlparse(website or "").netloc.lower().replace("www.", "")
    parts = base.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else base
    return bool(root) and host.endswith(root)
