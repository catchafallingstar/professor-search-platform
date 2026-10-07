"""Canonical professor-name handling.

University directories often append credentials ("A.J. Bauer, Ph.D.") or render
"Last, First" names.  External identity sources usually omit those decorations.
Keep one display name for the person, but compare/search using credential-free tokens.
"""

import re
import unicodedata

# Keys are punctuation-free lowercase forms.  Ambiguous ordinary words such as "ma"
# and "do" are only treated as credentials when the source token has a credential cue
# (comma-separated, ALL CAPS, internal capitals, or periods).
CREDENTIAL_KEYS = {
    "phd", "phdc", "md", "do", "dds", "dmd", "dvm", "scd", "dsc", "dphil", "edd", "jd",
    "ms", "msc", "mse", "ma", "mba", "mph", "med", "msw", "mshi", "me", "bs", "ba", "bsc",
    "pe", "cpa", "rn", "aia", "faia", "fasce", "fieee", "ncarb", "lpc", "ncc", "cccslp",
    "pt", "dpt", "dpm", "pharmd", "aud", "thd", "mpharm", "crnp", "cne", "faanp", "faan",
    "pmhnpbc", "dcsw", "lcsw", "cdp", "dma", "ofs", "sj", "std", "ssl", "rtr", "mha",
    "msph", "mphil", "mfa", "bfa", "dnp", "aprn", "np", "pmp", "mpp", "cph", "ocn", "cissp", "frcp", "facs",
    "fache", "rd", "ld", "otd", "otr", "rph", "bsee", "msee", "meng", "beng", "msn", "bsn",
}
GENERATION_KEYS = {"jr", "sr", "ii", "iii", "iv", "v"}
HONORIFICS = {"dr", "dr.", "prof", "prof.", "professor"}

ROLE_ONLY = re.compile(
    r"^(?:interim\s+)?(?:executive\s+|associate\s+|assistant\s+|senior\s+|master\s+)?"
    r"(?:dean|director|chair|lecturer|instructor|professor)(?:\s+emerit\w*)?$",
    re.I,
)
BAD_PREFIX = re.compile(r"^(?:speaker\s*:|seminar\s+topic\s*:)", re.I)
BAD_WORDS = re.compile(r"\b(?:faculty|staff|directory|department|office|news|research|about|team|program|"
                       r"center|institute|school|college|graduate|undergraduate|studies|media|affairs)\b", re.I)


def _key(token):
    return re.sub(r"[^a-z0-9]+", "", (token or "").lower())


def _credential_word(token):
    raw = (token or "").strip(" ,;:")
    key = _key(raw)
    if key not in CREDENTIAL_KEYS:
        return False
    # Prevent ordinary title-case surnames such as "Ma" and "Do" from disappearing.
    return (
        "." in raw or "(" in raw or ")" in raw or raw.isupper()
        or any(c.isupper() for c in raw[1:])
        or key in {"dphil", "phdc"}
    )


def _credential_segment(segment):
    words = [w for w in re.split(r"\s+", (segment or "").strip()) if w]
    return bool(words) and all(_credential_word(w) for w in words)


def clean_person_name(name):
    """Remove honorifics/credentials and normalize "Last, First" without guessing.

    Generational suffixes (Jr., III, ...) are retained for display, but are ignored by
    name_tokens() during identity matching.
    """
    s = " ".join((name or "").split()).strip(" ,;:-|")
    if not s:
        return ""

    words = s.split()
    if len(words) >= 3 and words[0].lower() in HONORIFICS:
        s = " ".join(words[1:]).strip()

    # Bad scrapes sometimes prepend the degree: "MD Ari Blitz", "ScD Xin Yu".
    words = s.split()
    if len(words) >= 3 and _credential_word(words[0]):
        s = " ".join(words[1:]).strip(" ,")

    # Remove comma-delimited credential chains: "Name, PhD, MBA".
    while "," in s:
        left, tail = s.rsplit(",", 1)
        if _credential_segment(tail.strip(" ;:")):
            s = left.strip(" ,;")
        else:
            break

    # Also handle a credential attached to the final name segment:
    # "Fink, Jennifer T. PhD" or "David Adler M.D.".
    while True:
        parts = s.rsplit(" ", 1)
        if len(parts) == 2 and _credential_word(parts[1]):
            s = parts[0].strip(" ,;")
        else:
            break

    # "Last, First M." -> "First M. Last", but "Name, III" is a generation suffix.
    if s.count(",") == 1:
        left, right = [x.strip() for x in s.split(",", 1)]
        if _key(right) not in GENERATION_KEYS and left and right and len(left.split()) <= 3:
            s = f"{right} {left}".strip()

    s = " ".join(s.split()).strip(" ,;")
    remain = s.replace(",", " ").split()
    if remain and all(_credential_word(w) or _key(w) in GENERATION_KEYS for w in remain):
        return ""
    return s


def normalize_text(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace(".", " ").replace(",", " ").replace("-", " ")
    return " ".join(text.split())


def name_tokens(name):
    cleaned = clean_person_name(name)
    toks = normalize_text(cleaned).split()
    if toks and toks[-1] in GENERATION_KEYS:
        toks = toks[:-1]
    return toks


def normalize_person_name(name):
    """Comparison key: credentials and generation suffixes are ignored."""
    return " ".join(name_tokens(name))


def storage_key(name):
    """Stable professor-ID key: credentials are removed but Jr./III/etc. stay distinct."""
    return normalize_text(clean_person_name(name))


def _token_compatible(a, b):
    if a == b:
        return True
    # An initial may match a full name; two different full names never do.
    if len(a) == 1 and b.startswith(a):
        return True
    if len(b) == 1 and a.startswith(b):
        return True
    return False


def names_match_strict(a_name, b_name):
    """High precision: surname + compatible given name; middle initials may be omitted.

    A.J. Bauer matches Andrew J. Bauer.  Licheng Liu does NOT match Lihong Liu.
    """
    a, b = name_tokens(a_name), name_tokens(b_name)
    if len(a) < 2 or len(b) < 2:
        return False
    if a == b:
        return True
    # Two-token reversed display forms.
    if len(a) == 2 and len(b) == 2 and _token_compatible(a[0], b[1]) and a[1] == b[0]:
        return True
    if a[-1] != b[-1] or not _token_compatible(a[0], b[0]):
        return False
    amid, bmid = a[1:-1], b[1:-1]
    if not amid or not bmid:
        return True
    return all(_token_compatible(x, y) for x, y in zip(amid, bmid))


def names_match(a_name, b_name):
    # Publication/ORCID/Scholar matching should not become looser than the strict
    # fallback.  Strong evidence comes from the source itself, not from first-initial guessing.
    return names_match_strict(a_name, b_name)


def looks_like_person(name):
    n = clean_person_name(name)
    if not n or BAD_PREFIX.search(n) or ROLE_ONLY.match(n) or BAD_WORDS.search(n):
        return False
    if any(c.isdigit() for c in n) or "@" in n or "/" in n:
        return False
    words = n.replace(",", " ").split()
    if len(words) < 2 or len(words) > 7:
        return False
    if all(_credential_word(w) for w in words):
        return False
    return True
