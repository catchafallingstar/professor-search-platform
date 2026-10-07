"""Academic-unit discovery: map a university's colleges / schools / departments, then look for a
faculty source inside EACH unit with its own page budget, and audit coverage.

Before: one 28-page budget per university, stop at the first directories found (EMU: only the
College of Education was ever found). Now:

  PASS 1  colleges & schools   - academics hub pages + homepage links + sitemap, then web search
  PASS 2  departments          - links on each college/school page (its own budget)
  PASS 3  faculty sources      - per unit: faculty/people links on the unit page, the unit's own
                                 /faculty, /people, /directory paths, then site: web search
  repeat until two rounds add no new units or directories (max 4 rounds)

Each unit is stored on the institution as institutions.units[] with a status:
  DIRECTORY_FOUND | NO_FACULTY_SOURCE | PENDING
and the university gets coverage = {units, with_directory, missing} so a partial crawl is shown as
PARTIAL_COVERAGE instead of looking complete. The department stored on each professor is the unit
name (the organisation tree), not a word taken from the URL.
"""
import re
import time
import urllib.parse

from services import fetchers as fx
from services import discovery as dsc

ROOT_BUDGET = 30          # pages to find the colleges / schools
UNIT_BUDGET = 8           # pages per college / school to find its departments
SOURCE_BUDGET = 6         # candidate directory pages validated per unit
MAX_UNITS = 120
MAX_ROUNDS = 4

_UNIT_NAME = re.compile(
    r"^(?:the\s+)?(?:(?:[A-Z][\w&'.-]*\s+){0,4})?(college|school|department|division|institute|program)\s+(?:of|for)\s+[\w&,'. -]{3,80}$"
    r"|^[\w&,'. -]{3,60}\s+(college|school)$|^[\w&,'. -]{3,60}\s+department$", re.I)
_NOT_UNIT = re.compile(r"\b(admission|apply|alumni|news|event|giving|donat|career|athletic|library|calendar|"
                       r"meeting|presentation|board|symposium|conference|workshop|seminar|town hall|"
                       r"forms?|documents?|resources?|center for|writing center|resource center|"
                       r"housing|dining|parking|visit|tour|student life|graduate school application|online|"
                       r"continuing|summer|camp|k-12|high school|law enforcement|police|graduate school|"
                       r"graduate college|graduate studies|honors college|honors program|extended|professional studies|"
                       r"undergraduate|graduate|majors?|minors?|degrees?|certificates?|academics?|research|faculty|staff)\b", re.I)

# Common department labels on college pages omit the word "Department" entirely.
_PLAIN_ACADEMIC = re.compile(
    r"^(accounting|africology|anthropology|architecture|art|arts|astronomy|biology|biochemistry|"
    r"business|chemistry|communication|communications|computer science|computing|criminology|"
    r"data science|economics|education|engineering|english|finance|geography|geology|history|"
    r"information security|information systems|linguistics|management|marketing|mathematics|math|"
    r"music|nursing|philosophy|physics|political science|psychology|public health|public policy|"
    r"sociology|statistics|theatre|theater|world languages|women'?s and gender studies)$", re.I)
_LINK = re.compile(r"\[([^\]]{2,120})\]\((https?://[^)\s]+)\)")
HUB_PATHS = ("/academics", "/academics/colleges", "/academics/colleges-schools", "/academics/schools-colleges",
             "/colleges", "/schools", "/colleges-and-schools", "/academics/departments", "/departments",
             "/academics/departments-programs", "/about/colleges")
FACULTY_PATHS = ("/faculty", "/people", "/directory", "/faculty-staff", "/faculty-and-staff", "/people/faculty",
                 "/about/faculty", "/about/people", "/our-faculty", "/faculty-directory")


def _clean(t):
    t = re.sub(r"[*_`#]+", "", re.sub(r"\s+", " ", t or "")).strip(" -|:")
    return t[:100]


def unit_type(name):
    n = name.lower()
    for k in ("college", "school", "department", "division", "institute", "program"):
        if re.search(rf"\b{k}\b", n):
            return k
    return "department"


def looks_like_unit(name, allow_plain=False):
    n = _clean(name)
    if not n or len(n) > 100 or _NOT_UNIT.search(n):
        return False
    return bool(_UNIT_NAME.match(n)) or bool(allow_plain and _PLAIN_ACADEMIC.match(n))


def _key(name):
    n = re.sub(r"^(the|gameabove|[a-z]+ family)\s+", "", name.lower())
    return re.sub(r"[^a-z]", "", n.replace("&", "and"))


class Map:
    """The university's academic units, keyed by normalised name."""

    def __init__(self, inst):
        self.inst = inst
        self.dom = dsc.domain_of(inst.get("official_website") or "")
        self.base = f"https://{urllib.parse.urlparse(inst.get('official_website') or '').netloc or self.dom}"
        self.units = {}
        for u in inst.get("units") or []:
            self.units[_key(u["name"])] = dict(u)

    def add(self, name, url, parent="", method="", allow_plain=False):
        name = _clean(name)
        if not looks_like_unit(name, allow_plain=allow_plain) or len(self.units) >= MAX_UNITS:
            return False
        if url and not dsc.on_domain(url, self.dom):
            url = ""
        k = _key(name)
        if k in self.units:
            u = self.units[k]
            if url and not u.get("url"):
                u["url"] = url
            return False
        self.units[k] = {"name": name, "type": unit_type(name), "url": url, "parent": parent,
                         "status": "PENDING", "directories": [], "found_via": method, "pages_used": 0}
        return True

    def list(self):
        return list(self.units.values())


_GENERIC_LINK = re.compile(r"^(website|visit( website)?|learn more|programs?|more|read more|explore|home|go|view|details)$", re.I)


def _links(url):
    """(name, url) pairs from a page. Besides plain links, a heading that names a unit followed by
    generic links ("## College of Business" / [Website](...) [Programs](...)) gives the heading as the
    name and the first link under it as the unit's URL."""
    page = fx.fetch_page_cached(url)
    if not page.get("ok"):
        return []
    text = page["text"]
    out = [(_clean(t), u.split("#")[0]) for t, u in _LINK.findall(text) if not _GENERIC_LINK.match(_clean(t))]
    # heading-then-links pattern (multi-line headings are joined)
    for m in re.finditer(r"(?m)^#{1,4}\s+(.+(?:\n(?!\s*\n|#|\[).+)?)\s*\n+((?:\s*\[[^\]]*\]\([^)]+\)\s*\n*){1,4})", text):
        name = _clean(m.group(1).replace("\n", " "))
        first = _LINK.search(m.group(2)) or re.search(r"\[([^\]]*)\]\((https?://[^)\s]+)\)", m.group(2))
        if name and first:
            out.append((name, first.group(2).split("#")[0]))
    return out


def pass_colleges(m, log=print):
    """PASS 1: colleges and schools from hub pages, the homepage, and a web search."""
    budget = ROOT_BUDGET
    new = 0
    for path in ("",) + HUB_PATHS:
        if budget <= 0:
            break
        budget -= 1
        for text, url in _links(m.base + path):
            if dsc.on_domain(url, m.dom) and unit_type(text) in ("college", "school") and m.add(text, url, method="hub_link"):
                new += 1
        time.sleep(0.5)
    if sum(1 for u in m.list() if u["type"] in ("college", "school")) < 2:
        from services import websearch
        for q in (f"site:{m.dom} colleges and schools", f"{m.inst['name']} colleges schools list"):
            try:
                for r in websearch.search(q, 10):
                    if dsc.on_domain(r["url"], m.dom):
                        for text, url in _links(r["url"])[:300]:
                            if unit_type(text) in ("college", "school") and m.add(text, url, method="search"):
                                new += 1
                        break
            except websearch.SearchUnavailable:
                break
    log(f"{m.inst['name']}: {new} colleges/schools found")
    return new


def pass_departments(m, log=print):
    """PASS 2: departments listed on each college / school page (budget per college)."""
    new = 0
    for u in [x for x in m.list() if x["type"] in ("college", "school") and x.get("url") and not x.get("expanded")]:
        pages = [u["url"]] + [u["url"].rstrip("/") + p for p in ("/departments", "/academics", "/academics/departments")]
        for page in pages[:UNIT_BUDGET]:
            for text, url in _links(page):
                if dsc.on_domain(url, m.dom) and unit_type(text) in ("department", "school", "division", "program") \
                        and m.add(text, url, parent=u["name"], method="unit_page", allow_plain=True):
                    new += 1
            time.sleep(0.5)
        u["expanded"] = True
    log(f"{m.inst['name']}: {new} departments found inside colleges")
    return new


def _source_candidates(m, u):
    """Faculty-list candidates for one unit: links on its page, its standard paths, then web search."""
    cands = []
    if u.get("url"):
        for text, url in _links(u["url"]):
            path = urllib.parse.urlparse(url).path.lower()
            if dsc.on_domain(url, m.dom) and not dsc.is_news_like(url) and \
                    (re.search(r"/(faculty|people|directory|staff)", path) or re.search(r"\b(faculty|people|directory)\b", text, re.I)):
                cands.append(url)
        root = u["url"].split("?")[0].rstrip("/")
        root = re.sub(r"/(index|default)\.\w+$", "", root)
        cands += [root + p for p in FACULTY_PATHS]
    if len(cands) < 3:
        from services import websearch
        name = re.sub(r"^(department|school|college) of\s+", "", u["name"], flags=re.I)
        try:
            for r in websearch.search(f'site:{m.dom} "{name}" faculty', 8):
                if dsc.on_domain(r["url"], m.dom) and not dsc.is_news_like(r["url"]):
                    cands.append(r["url"])
        except websearch.SearchUnavailable:
            pass
    out = []
    for c in cands:
        c = c.split("#")[0]
        if c not in out:
            out.append(c)
    out.sort(key=lambda x: -dsc.score(x, ""))
    return out


def pass_sources(m, known_dirs, log=print):
    """PASS 3: a faculty directory for every unit that has none yet (each unit has its own budget)."""
    new = 0
    for u in m.list():
        if u["status"] != "PENDING":
            continue
        used, found = 0, []
        for url in _source_candidates(m, u):
            if used >= SOURCE_BUDGET:
                break
            if url.rstrip("/") in known_dirs:
                found.append(url)
                continue
            used += 1
            ok, rows, info = dsc.validate(url, u["name"])
            if ok:
                found.append(url)
                known_dirs.add(url.rstrip("/"))
                new += 1
                break                       # one good list per unit; profiles expand it later
            time.sleep(0.5)
        u["pages_used"] = u.get("pages_used", 0) + used
        u["directories"] = found
        u["status"] = "DIRECTORY_FOUND" if found else "NO_FACULTY_SOURCE"
    log(f"{m.inst['name']}: {new} new faculty directories from academic units")
    return new


_FAC_FOLDER = re.compile(r"^(/(?:[\w.-]+/){1,3}?)(faculty|faculty-staff|faculty-and-staff|people|directory)/", re.I)
# folders that hold faculty RESOURCES (forms, documents, support offices), not faculty members
_NOT_FAC_FOLDER = re.compile(r"/(documents?|forms?|resources?|policies|handbook|news|events?|why-[\w-]+|"
                             r"registrar|human-resources|academic-human-resources|hr|it|starfish|drc|"
                             r"disability[\w-]*|writing[\w-]*|uwc|ccw|library|advising|career[\w-]*|"
                             r"admissions?|financial-aid|finaid|bookstore|parking|police|foundation)/", re.I)
_PERSON_URL = re.compile(r"/[a-z]{1,3}[-_][a-z][\w-]+\.(php|html?|aspx)$|/[a-z]+-[a-z]+(-[a-z]+)?/?$", re.I)


def pass_sitemap_folders(m, known_dirs, log=print):
    """PASS 4: infer faculty sources from every profile-heavy folder in recursive sitemaps."""
    folders = {}
    for loc in dsc.sitemap_urls(m.base, m.dom):
        path = urllib.parse.urlparse(loc).path
        mm = _FAC_FOLDER.match(path)
        if not mm or _NOT_FAC_FOLDER.search(mm.group(0)):
            continue
        # Only direct children of the faculty/people folder are treated as profile candidates.
        if path.count("/") > mm.group(0).count("/") + 1:
            continue
        folders.setdefault(mm.group(0), [0, 0])
        folders[mm.group(0)][0] += 1
        if _PERSON_URL.search(path):
            folders[mm.group(0)][1] += 1

    new = 0
    for folder, (n, persons) in sorted(folders.items(), key=lambda x: -x[1][0]):
        if n < 3 or persons < 3:
            continue
        listing = f"{m.base}{folder}"
        if any(k.startswith(listing.rstrip("/")) for k in known_dirs):
            continue
        unit_root = folder[: -len(folder.rstrip("/").rsplit("/", 1)[-1]) - 1] or "/"
        page = fx.fetch_page_cached(m.base + unit_root)
        title = re.split(r"\s+[|\-–]\s+", (page.get("title") or "").strip())[0].strip()
        uni_word = (m.inst.get("name") or "").split()[0].lower()
        bad = not title or len(title) >= 80 or title.lower().startswith(uni_word) or title.lower() in ("home", "index")
        name = unit_root.strip("/").split("/")[-1].replace("-", " ").title() if bad else title
        name = re.split(r"\s+at\s+" + re.escape(uni_word), name, flags=re.I)[0].strip()
        if _NOT_UNIT.search(name):
            continue
        k = _key(name)
        if k not in m.units:
            m.units[k] = {"name": name, "type": unit_type(name), "url": m.base + unit_root, "parent": "",
                          "status": "PENDING", "directories": [], "found_via": "sitemap_folder",
                          "pages_used": 0}
        u = m.units[k]
        if u["status"] != "DIRECTORY_FOUND":
            u["directories"] = [listing]
            u["status"] = "DIRECTORY_FOUND"
            u["profile_count"] = n
            known_dirs.add(listing.rstrip("/"))
            new += 1
    log(f"{m.inst['name']}: {new} faculty folders found in recursive sitemaps")
    return new


def build(inst, existing_dirs=(), log=print):
    """Run the passes until stable. Returns (units, directories [[unit name, url]], coverage)."""
    m = Map(inst)
    known = {d[1].rstrip("/") for d in existing_dirs}
    stable = 0
    for rnd in range(MAX_ROUNDS):
        before = (len(m.units), len(known))
        if rnd == 0 or stable == 0:
            pass_colleges(m, log) if rnd == 0 else None
            pass_departments(m, log)
        # units that failed to find a source get one more try in a later round
        if rnd > 0:
            for u in m.list():
                if u["status"] == "NO_FACULTY_SOURCE" and u.get("retries", 0) < 1:
                    u["status"], u["retries"] = "PENDING", u.get("retries", 0) + 1
        pass_sources(m, known, log)
        if rnd == 0:
            pass_sitemap_folders(m, known, log)
        stable = stable + 1 if (len(m.units), len(known)) == before else 0
        if stable >= 2:
            break
    units = m.list()
    dirs = []
    for u in units:
        for url in u.get("directories") or []:
            dirs.append([u["name"], url])
    # existing directory pages keep their place (curated lists, earlier discoveries)
    have = {d[1].rstrip("/") for d in dirs}
    for d in existing_dirs:
        if d[1].rstrip("/") not in have:
            dirs.append(list(d))
    teaching = [u for u in units if u["type"] in ("department", "school", "division", "program")] or units
    with_dir = [u for u in teaching if u["status"] == "DIRECTORY_FOUND"]
    coverage = {"units": len(teaching), "with_directory": len(with_dir),
                "missing": [u["name"] for u in teaching if u["status"] != "DIRECTORY_FOUND"][:60],
                "empty_directories": [], "readable_units": 0,
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    return units, dirs, coverage


def apply_extraction_results(units, coverage, report):
    """Fold the actual faculty extraction outcome back into coverage.

    A directory URL existing is not sufficient evidence of coverage: bot checks and JS-only
    listings can yield zero people. Those units remain partial until a crawl can read them.
    """
    cov = dict(coverage or {})
    by_url = {r.get("url", "").rstrip("/"): r for r in (report or [])}
    empty, readable = [], 0
    for u in units or []:
        if u.get("type") not in ("department", "school", "division", "program"):
            continue
        dirs = u.get("directories") or []
        results = [by_url.get(x.rstrip("/")) for x in dirs if by_url.get(x.rstrip("/"))]
        found = max([int(r.get("found", 0)) for r in results] or [0])
        u["faculty_found"] = found
        u["extraction_status"] = "READABLE" if found > 0 else ("EMPTY_OR_BLOCKED" if dirs else "NO_DIRECTORY")
        if found > 0:
            readable += 1
        elif dirs:
            empty.append(u.get("name", ""))
    cov["readable_units"] = readable
    cov["empty_directories"] = [x for x in empty if x][:60]
    cov["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return units, cov


def coverage_state(cov, n_professors):
    """University state from coverage: discovery failed / partial / complete."""
    if not cov or cov.get("units", 0) == 0:
        return "DISCOVERY_FAILED" if n_professors == 0 else "UNMAPPED"
    if cov.get("with_directory", 0) < cov.get("units", 0):
        return "PARTIAL_COVERAGE"
    if cov.get("empty_directories"):
        return "PARTIAL_COVERAGE"
    return "COMPLETE"
