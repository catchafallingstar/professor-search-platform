"""Publications and grants read from the professor's OWN official faculty page.

Used when no external identity resolves (no OpenAlex author, no Google Scholar, no ORCID) - common
for humanities and arts faculty, whose books, poems, translations and essays are not indexed.

Rules (no model, no guessing):
  * publications: items listed under a "Publications" / "Books" / "Selected works" heading
  * grants/fellowships: sentences on the page naming a funder plus "fellowship"/"grant"/"award",
    each kept with the exact sentence as evidence (e.g. "Funded by a National Endowment for the
    Arts Creative Writing Fellowship in Poetry")
Every record keeps the faculty page URL as its source.
"""

import hashlib
import re

from services import fetchers as fx

PUB_HEAD = re.compile(r"(?im)^\s*(?:\*\*|#+\s*)?\s*((?:selected\s+|recent\s+|book\s+|other\s+)?(?:publications|books|selected works|works|writings|articles|translations)\s*:?)\s*(?:\*\*)?\s*:?\s*$")
ANY_HEAD = re.compile(r"(?m)^\s*(?:\*\*[^*\n]{2,60}\*\*:?|#+\s+\S.*|[A-Z][A-Za-z &/]{2,40}:)\s*$")
ITEM = re.compile(r"(?m)^\s*(?:[*\-\u2022]|\d+\.)\s+(.{12,400})$")
YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
QUOTED = re.compile(r"[\"\u201c\u201d']{1,2}\s*\[?([^\"\u201c\u201d\]]{3,200}?)\]?\s*(?:\([^)]*\))?\s*[\"\u201c\u201d']{1,2}")

FUNDERS = (
    "National Endowment for the Arts", "National Endowment for the Humanities", "National Science Foundation",
    "National Institutes of Health", "Guggenheim", "Mellon", "Fulbright", "MacArthur", "American Council of Learned Societies",
    "ACLS", "Ford Foundation", "Rockefeller", "Spencer Foundation", "Carnegie", "Sloan", "Radcliffe", "Whiting",
    "Howard Foundation", "Social Science Research Council", "Wenner-Gren", "NEH", "NEA", "NSF", "NIH",
    "Department of Energy", "Department of Defense", "DARPA", "Simons Foundation", "Gates Foundation",
)
GRANT_WORDS = re.compile(r"\b(fellowship|grant|award(?:ed)?|funded by|funding from|supported by|prize)\b", re.I)


def _clean(s):
    s = LINK.sub(r"\1", s)
    s = re.sub(r"[_*`]+", "", s)
    return re.sub(r"\s+", " ", s).strip(" .;,")


def _title_of(item):
    m = QUOTED.search(LINK.sub(r"\1", item))
    t = m.group(1) if m else re.split(r"\s[\(\-\u2013\u2014]|\.\s", _clean(item))[0]
    return _clean(t)[:250]


def publications(text, limit=40):
    """[{title, year, venue_text, url}] listed under a publications-type heading."""
    out, seen = [], set()
    for h in PUB_HEAD.finditer(text):
        start = h.end()
        nxt = [m.start() for m in ANY_HEAD.finditer(text, start) if m.start() > start + 5 and not PUB_HEAD.match(text, m.start())]
        block = text[start:min(nxt) if nxt else start + 12000]
        for m in ITEM.finditer(block):
            raw = m.group(1)
            title = _title_of(raw)
            if len(title) < 3 or title.lower() in seen:
                continue
            seen.add(title.lower())
            yr = YEAR.findall(raw)
            link = LINK.search(raw)
            out.append({"title": title, "year": int(yr[-1]) if yr else 0, "venue_text": _clean(raw)[:300],
                        "url": link.group(2) if link else ""})
            if len(out) >= limit:
                return out
    return out


def grants(text, limit=10):
    """[{title, funder_name, evidence, year}] - fellowships/grants named on the page."""
    out, seen = [], set()
    flat = re.sub(r"\s+", " ", LINK.sub(r"\1", text))
    for sent in re.split(r"(?<=[.!?])\s+", flat):
        if not GRANT_WORDS.search(sent):
            continue
        funder = next((f for f in FUNDERS if re.search(r"\b" + re.escape(f) + r"\b", sent)), "")
        if not funder:
            continue
        # title = the funder's name through the award word, e.g. "National Endowment for the Arts
        # Creative Writing Fellowship in Poetry"
        i = sent.find(funder)
        m = re.search(r"(Fellowship|Grant|Award|Prize)(\s+(?:in|for)\s+[A-Z][\w\-]*(?:\s+[A-Z][\w\-]*){0,3})?", sent[i:])
        title = _clean(sent[i:i + m.end()]) if m and m.start() < 120 else f"{funder} grant"
        key = (funder.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        yr = YEAR.findall(sent)
        out.append({"title": title[:200], "funder_name": funder, "evidence": _clean(sent)[:400], "year": int(yr[-1]) if yr else 0})
        if len(out) >= limit:
            break
    return out


def from_page(url):
    """Fetch the faculty page once and return (publications, grants, ok)."""
    page = fx.fetch_page(url)
    if not page.get("ok"):
        return [], [], False
    return publications(page["text"]), grants(page["text"]), True


def item_id(prefix, prof_id, title):
    return prefix + hashlib.sha1(f"{prof_id}|{title.lower()}".encode()).hexdigest()[:16]
