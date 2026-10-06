"""Find a university's faculty-directory pages WITHOUT AI first.

Order (stops as soon as enough valid directories are found or the budget is spent):
  1. robots.txt "Sitemap:" lines + common sitemap paths -> URLs containing faculty/people/directory
  2. homepage and a few academic hub pages (/academics, /departments, /schools ...) -> internal links
     scored by anchor text and URL pattern; academic-unit links followed up to depth 3
  3. every candidate is FETCHED and validated (>= 5 plausible names, >= 3 professor titles,
     on the university's domain) before it is accepted
Only when this finds nothing does the caller fall back to the LLM URL guess (also validated).
"""

import re
import time
import urllib.parse
import urllib.request
import gzip
import io

from services import fetchers as fx

MAX_PAGES = 30        # pages fetched per university for discovery
MAX_DEPTH = 3
ENOUGH = 12           # stop after this many valid directories

HIGH_TEXT = ("faculty directory", "our faculty", "faculty & staff", "faculty and staff", "meet the faculty",
             "academic faculty", "research faculty", "professors", "faculty", "people", "directory")
HIGH_PATH = ("/faculty-directory", "/people/faculty", "/our-faculty", "/academics/faculty", "/about/faculty",
             "/faculty", "/people", "/directory")
MID_PATH = ("/department", "/departments", "/school", "/college", "/researchers", "/members", "/team")
LOW_PATH = ("/admissions", "/alumni", "/news", "/events", "/giving", "/athletics", "/student-life",
            "/campus-life", "/jobs", "/hr", "/calendar", "/apply", "/visit", "/library", "/covid")
UNIT_WORDS = ("college of", "school of", "department of", "engineering", "computer", "computing", "science",
              "mathematics", "statistics", "physics", "chemistry", "biology", "robotics", "data", "information",
              "electrical", "mechanical", "aerospace", "materials", "chemical", "biomedical", "civil", "economics",
              # liberal arts, humanities and social sciences are in scope too
              "liberal arts", "humanities", "arts", "letters", "english", "history", "philosophy", "language",
              "literature", "linguistics", "classics", "religion", "art", "music", "theatre", "theater", "film",
              "media", "communication", "journalism", "anthropology", "sociology", "political", "psychology",
              "social", "education", "law", "business", "public policy", "studies", "geography")
HUBS = ("", "/academics", "/academics/schools-colleges", "/schools-colleges", "/colleges", "/departments",
        "/academics/departments", "/research")
LINK_RE = re.compile(r"\[([^\]]{1,120})\]\((https?://[^)\s]+)\)")


# Campus news, department announcements, student blogs, events: never a faculty directory.
NEWS_RE = re.compile(
    r"/(news|newsroom|stories|story|blog|blogs|posts?|articles?|announcements?|press|press-releases|"
    r"events?|calendar|spotlights?|features?|magazine|media|category|tag|author|feed|student-life|"
    r"students?/blog|in-the-news)(/|$)"
    r"|/(19|20)\d\d[/-]\d{1,2}([/-]\d{1,2})?(/|-|$)"       # /2024/05/... or /2026-04-08-...
    r"|[?&](p|cat|tag)=", re.I)
DIR_WORDS = ("faculty", "people", "directory", "staff", "researchers", "members", "professors")


def is_news_like(url):
    p = urllib.parse.urlparse(url or "")
    path = p.path
    if NEWS_RE.search(path + ("?" + p.query if p.query else "")):
        return True
    # A directory page's own slug names the page ("faculty", "our-faculty"); an article's slug
    # tells a story ("for-the-glory-2026-aaas-fellows"). 5+ hyphenated words with none of the
    # directory words present reads as a headline, not a listing.
    last = re.sub(r"\.\w+$", "", path.rsplit("/", 1)[-1])
    words = [w for w in last.split("-") if w]
    if len(words) >= 5 and not any(w in DIR_WORDS for w in words):
        return True
    return False


def wordpress_directory_pages(base, dom):
    """Ask a WordPress site's REST API which of its PAGES are faculty directories.
    Only the `pages` type is queried; `posts` (news, announcements, blog entries) never are.
    Returns candidate page URLs; the caller still fetches and validates each real page."""
    out = []
    for term in ("faculty", "people", "directory"):
        api = f"{base}/wp-json/wp/v2/pages?search={term}&per_page=50&_fields=link,title,type"
        try:
            data = fx._get_json_any(api)
        except Exception:
            return out                     # not WordPress, or the API is closed
        if not isinstance(data, list):
            return out
        for d in data:
            link = (d.get("link") or "").strip()
            title = re.sub(r"<[^>]+>", "", ((d.get("title") or {}).get("rendered") or "")).strip()
            path = urllib.parse.urlparse(link).path.lower()
            if (d.get("type", "page") == "page" and on_domain(link, dom) and not is_news_like(link)
                    and re.search(r"/(faculty|people|directory)", path) and path.count("/") <= 5):
                out.append((title, link))
        time.sleep(0.5)
    return list(dict.fromkeys(out))


def domain_of(website):
    host = urllib.parse.urlparse(website or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def on_domain(url, dom):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    return bool(dom) and (host == dom or host.endswith("." + dom))


def score(url, text):
    path = urllib.parse.urlparse(url).path.lower().rstrip("/")
    t = (text or "").lower()
    s = 0
    if "/faculty" in path:
        s += 100
    if "faculty" in t:
        s += 90
    if "/people" in path:
        s += 80
    if "people" in t:
        s += 70
    if "/directory" in path:
        s += 60
    if any(w in t for w in UNIT_WORDS):
        s += 30
    if any(m in path for m in MID_PATH):
        s += 15
    if any(l in path for l in LOW_PATH):
        s -= 70
    if path.count("/") > 5:
        s -= 20          # deep paths are usually single profiles / news items
    return s


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": fx.BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(5_000_000)
    if url.endswith(".gz"):
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return raw.decode("utf-8", "ignore")


def sitemap_candidates(base, dom, limit=4000):
    """Faculty-like URLs from robots.txt sitemaps and common sitemap locations."""
    maps = []
    try:
        for line in _get(base + "/robots.txt").splitlines():
            if line.lower().startswith("sitemap:"):
                maps.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    maps += [base + p for p in ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml")]
    seen, urls = set(), []
    while maps and len(seen) < 12:
        m = maps.pop(0)
        if m in seen:
            continue
        seen.add(m)
        try:
            xml = _get(m)
        except Exception:
            continue
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)[:limit]:
            if loc.endswith((".xml", ".xml.gz")):
                if re.search(r"page|people|faculty|profile|post", loc, re.I):
                    maps.append(loc)
                continue
            p = urllib.parse.urlparse(loc).path.lower()
            if on_domain(loc, dom) and not is_news_like(loc) and re.search(r"/(faculty|people|directory)(/|$|\.html)", p) and p.count("/") <= 4:
                urls.append(loc)
    return list(dict.fromkeys(urls))


def sitemap_profiles(directory_url, limit=400):
    """Profile pages that live under a directory page's folder, taken from the site's sitemaps.
    For listings that load names with JavaScript in pages ("1 2 3 ... 12 Next"), where reading the
    page only ever returns the first screen (e.g. emich.edu/coe/faculty-staff/index.php)."""
    p = urllib.parse.urlparse(directory_url)
    folder = p.path.rsplit("/", 1)[0] + "/"
    dom = domain_of(f"{p.scheme}://{p.netloc}")
    base = f"{p.scheme}://{p.netloc}"
    maps = []
    try:
        for line in fx.fetch_page(base + "/robots.txt").get("text", "").splitlines():
            if line.lower().startswith("sitemap:"):
                maps.append(line.split(":", 1)[1].strip())
    except Exception:
        pass
    maps += [base + "/sitemap.xml"]
    out, seen = [], set()
    for m in list(dict.fromkeys(maps))[:6]:
        if m in seen:
            continue
        seen.add(m)
        text = ""
        try:
            text = _get(m)
        except Exception:
            text = fx.fetch_page(m).get("text", "")          # bot-protected sites: via the reader
        for loc in re.findall(r"https?://[^\s<>\"')\]]+", text):
            lp = urllib.parse.urlparse(loc)
            if (on_domain(loc, dom) and lp.path.startswith(folder) and lp.path != p.path
                    and not lp.path.endswith(("index.php", "index.html", "/")) and lp.path.count("/") == folder.count("/")):
                out.append(loc.split("#")[0])
    return list(dict.fromkeys(out))[:limit]


def looks_paginated(page_text):
    """True when a listing shows more pages than were read: numbered page links / "Next page"."""
    t = page_text or ""
    nums = [int(n) for n in re.findall(r"\[(\d{1,3})\]\([^)]*\)", t)]
    return bool(re.search(r"\[next( page)?\]", t, re.I)) or (len(nums) >= 3 and max(nums) >= 3)


def validate(url, dept=""):
    """Fetch and check a candidate. Returns (ok, rows, info)."""
    if is_news_like(url):
        return False, [], {"name_count": 0, "faculty_title_count": 0, "via": "rejected_news"}
    rows, via = fx.extract_faculty_any(url, dept or "Faculty")
    titles = sum(1 for r in rows if "professor" in (r.get("title") or "").lower())
    ok = len(rows) >= 5 and titles >= 3
    return ok, rows, {"name_count": len(rows), "faculty_title_count": titles, "via": via}


def _dept_label(text, url):
    t = re.sub(r"\s+", " ", text or "").strip()
    if t and t.lower() not in ("faculty", "people", "directory", "our faculty", "faculty directory", "faculty & staff"):
        return t[:80]
    # path words that name the page, not the department ("index.php", "faculty-staff")
    skip = ("people", "faculty", "directory", "staff", "faculty-staff", "faculty-and-staff", "our-faculty", "listing", "listings")
    parts = [re.sub(r"\.\w+$", "", p) for p in urllib.parse.urlparse(url).path.split("/")]
    parts = [p for p in parts if p and p.lower() not in skip and p.lower() not in ("index", "default", "home")]
    host = urllib.parse.urlparse(url).netloc.split(".")[0]
    label = (parts[-1] if parts else host).replace("-", " ").replace("_", " ")
    return label.title()[:80]


def discover(inst, log=print):
    """Returns [{"department","url","discovery_method","name_count","faculty_title_count"}]."""
    site = inst.get("official_website") or ""
    dom = domain_of(site)
    if not dom:
        return []
    base = f"https://{urllib.parse.urlparse(site).netloc or dom}"
    accepted, tried = [], set()
    budget = [MAX_PAGES]

    def try_url(url, text, method):
        key = url.split("#")[0].rstrip("/")
        if key in tried or budget[0] <= 0 or len(accepted) >= ENOUGH:
            return
        tried.add(key)
        budget[0] -= 1
        ok, rows, info = validate(url, _dept_label(text, url))
        if ok:
            accepted.append(dict(info, department=_dept_label(text, url), url=url, discovery_method=method))
        time.sleep(1.0)

    # 1. sitemaps
    for u in sitemap_candidates(base, dom)[:15]:
        try_url(u, "", "sitemap")
    # 2. homepage + academic hubs, then follow academic-unit links (bounded)
    frontier = [(base + h, 0) for h in HUBS]
    visited = set()
    while frontier and budget[0] > 0 and len(accepted) < ENOUGH:
        url, depth = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        page = fx.fetch_page(url)
        budget[0] -= 1
        if not page.get("ok"):
            continue
        links = [(m.group(1), m.group(2)) for m in LINK_RE.finditer(page["text"])]
        cands = sorted({(t, u) for t, u in links if on_domain(u, dom) and not is_news_like(u)},
                       key=lambda x: -score(x[1], x[0]))
        for t, u in cands[:12]:
            s = score(u, t)
            path = urllib.parse.urlparse(u).path.lower()
            if s >= 80 and re.search(r"/(faculty|people|directory)", path):
                try_url(u, t, "homepage_link" if depth == 0 else "academic_unit_link")
            elif s >= 30 and depth + 1 < MAX_DEPTH and any(w in t.lower() for w in UNIT_WORDS):
                frontier.append((u, depth + 1))
    # 3. WordPress REST API as a pointer only: it names which PAGES are directories, and each
    #    of those real pages is then fetched and validated like any other candidate.
    if not accepted:
        budget[0] = max(budget[0], 8)
        for t, u in wordpress_directory_pages(base, dom)[:8]:
            try_url(u, t, "wordpress_page_index")
    log(f"{inst.get('name')}: discovery tried {MAX_PAGES - budget[0]} pages, {len(accepted)} directories")
    return accepted
