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

WORKER = {"running": False, "thread": None, "token": ""}


def set_worker_token(token):
    """Internal secret the worker sends to /function/process_next_step (it is not a logged-in user)."""
    WORKER["token"] = token or ""


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


LAST_SEARCH = {"t": 0.0}


def note_search():
    """Called by the search endpoint: the worker pauses so visitors are served first."""
    LAST_SEARCH["t"] = time.time()


def _worker_loop():
    base = None
    time.sleep(5)
    while WORKER["running"]:
        # The worker and the website share one server process. Give searches the CPU:
        # hold off while anyone searched in the last 30 s, and rest 3 s between steps.
        while time.time() - LAST_SEARCH["t"] < 30 and WORKER["running"]:
            time.sleep(2)
        time.sleep(3)
        base = base or _local_api()
        if not base:
            time.sleep(5)
            continue
        try:
            payload = json.dumps({"worker_token": WORKER["token"]}).encode("utf-8")
            req = urllib.request.Request(base + "/function/process_next_step", data=payload,
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


def call_internal(function_name, params=None, timeout=600):
    """Run a pipeline function as the internal worker (anonymous request + worker token).

    Writes to the shared database only succeed from the shared (guest) context: a signed-in
    staff user's request runs on their own root and is denied write access to shared nodes.
    Staff endpoints therefore check require_staff() and then delegate here.
    """
    base = _local_api()
    if not base:
        raise RuntimeError("Local API not reachable")
    body = dict(params or {})
    body["worker_token"] = WORKER["token"]
    req = urllib.request.Request(base + "/function/" + function_name, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(((payload.get("error") or {}).get("message")) or "Internal call failed")
    return (payload.get("data") or {}).get("result")


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


def ensure_owner_account():
    """Provision the owner through Jac's current PostgreSQL-backed UserManager.

    OWNER_PASSWORD is only used at account creation and is never logged.
    If the account already exists, do not reset or replace its credentials.
    """
    email = os.environ.get("OWNER_EMAIL", "bxybai@umich.edu").strip().lower()
    password = os.environ.get("OWNER_PASSWORD", "")
    if not password:
        print("[auth] OWNER_PASSWORD not set; owner auto-provisioning disabled.")
        return
    try:
        from jaclang.server.identity.user_manager import UserManager
        created = UserManager().create_user(email, password)
        if created and created.get("user_id"):
            print(f"[auth] owner account {email} provisioned.")
        else:
            print("[auth] owner not provisioned; check existing account or identity backend.")
    except Exception as exc:
        print(f"[auth] owner not provisioned: {type(exc).__name__}: {str(exc)[:120]}")


def _accounts_backup_loop():
    """Copy new sign-ups to MongoDB every 2 minutes (see services/accounts_mirror.py)."""
    from services import accounts_mirror as am
    while True:
        try:
            am.backup()
        except Exception as e:
            print(f"[auth] account backup failed: {e}")
        time.sleep(120)


def restore_then_resume():
    """On server boot: recreate the owner account, restore the saved directory into the
    (possibly empty) graph, re-index, queue the priority universities and start the worker.
    Nobody has to sign in for the pipeline to resume after a sandbox reset.
    Set PIPELINE_AUTOSTART=0 to keep the worker stopped on boot.
    Runs once per server process: hot reloads re-execute the Jac entry block, and stacked
    boot routines (each re-indexing every professor) starved search of CPU."""
    if WORKER.get("booted"):
        return
    WORKER["booted"] = True

    def _go():
        for _ in range(60):          # wait for the API to come up (max ~2 min)
            if _local_api():
                break
            time.sleep(2)
        time.sleep(8)                # the account system finishes initialising after /healthz answers
        try:
            from services import accounts_mirror as am
            print(f"[auth] restored {am.restore()} accounts from MongoDB.")
            print(f"[auth] repaired {am.repair_roots()} missing user roots.")
        except Exception as e:
            print(f"[auth] account restore skipped: {e}")
        try:
            ensure_owner_account()
        except Exception as e:
            print(f"[auth] owner bootstrap failed: {e}")
        threading.Thread(target=_accounts_backup_loop, daemon=True).start()
        # The directory lives in MongoDB (services/store.py): nothing to restore. Load the IPEDS
        # university list if the database is empty, then start the pipeline worker.
        # MongoDB can be unreachable at boot (wrong/changed password, network blip). Keep retrying
        # every 30 s instead of giving up for the life of the server; a fixed URI in Settings
        # (or .env) is picked up on the next attempt without a restart.
        attempt = 0
        while True:
            attempt += 1
            try:
                from services import store as st, pipe
                st.reset_connection()
                st.db()
                if st.count_institutions() == 0:
                    year, n, new = pipe.load_ipeds()
                    pipe.log(f"Loaded {n} research universities from IPEDS {year}.")
                try:
                    removed = st.prune_transient()
                    if any(removed.values()):
                        print(f"[store] pruned rebuildable records: {removed}")
                except Exception as e:
                    print(f"[store] transient-data prune skipped: {str(e)[:120]}")
                print(f"[store] MongoDB directory: {st.overview()}")
                autostart = os.environ.get("PIPELINE_AUTOSTART", "1").strip() not in ("0", "false", "no")
                if autostart and st.get_setting("worker_running", True) is not False:
                    pipe.start()
                    pipe.log("Auto-processing started.")
                break
            except Exception as e:
                print(f"[store] MongoDB directory unavailable (attempt {attempt}, retrying in 30 s): {str(e)[:160]}")
                time.sleep(30)
    threading.Thread(target=_go, daemon=True).start()


def resume_if_flagged():
    if os.path.exists(_RUN_FLAG) and not WORKER["running"]:
        start_worker()


def worker_running():
    return WORKER["running"]


def llm_configured():
    """True when an LLM is available (needed for faculty-page extraction and hiring research):
    a cloud key, or a local Ollama model (LLM_MODEL=ollama/... plus OLLAMA_API_BASE)."""
    ollama = os.environ.get("LLM_MODEL", "").startswith("ollama") and bool(os.environ.get("OLLAMA_API_BASE"))
    return ollama or bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                          or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def llm_reachable():
    """For Ollama: is the tunnel to the other computer up? Returns (ok, message)."""
    base = os.environ.get("OLLAMA_API_BASE", "").strip().rstrip("/")
    if not base:
        return (llm_configured(), "cloud model" if llm_configured() else "no LLM configured")
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/api/tags", headers={"User-Agent": "curl/8.5.0"}), timeout=10) as r:
            models = [m.get("name") for m in json.loads(r.read().decode()).get("models", [])]
        want = os.environ.get("LLM_MODEL", "").replace("ollama/", "").replace("ollama_chat/", "")
        if want and want not in models and want + ":latest" not in models:
            return (False, f"Ollama is reachable but model '{want}' is not pulled (have: {', '.join(models[:8])})")
        return (True, f"Ollama reachable, {len(models)} models")
    except Exception as e:
        return (False, f"Ollama not reachable at {base}: {str(e)[:120]}")


# ---------------- OpenAlex credential pool (see services/keypool.py) ----------------
try:
    from services import keypool as kp
except ImportError:  # imported as a plain module
    import keypool as kp

RateLimited = kp.RateLimited
add_key = kp.add_key
remove_key = kp.remove_key
reset_key = kp.reset_key
key_rows = kp.key_rows


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
    return kp.status()


def config_status():
    rl = kp.status()
    return {
        "openalex_ok": not rl["limited"],
        "openalex_mailto": bool(os.environ.get("OPENALEX_MAILTO")),
        "openalex_key": len(kp.configured_keys()) > 0,
        "openalex_keys_total": len(kp.configured_keys()),
        "openalex_keys_available": rl["keys_available"],
        "openalex_keys_invalid": kp.invalid_count(),
        "llm_key": llm_configured(),
    }


def _get_json(path, params=None):
    """One OpenAlex GET. Picks the best credential, rotates on 429/401/403, raises RateLimited when all rest."""
    mail = os.environ.get("OPENALEX_MAILTO", "")
    for _hop in range(len(kp.configured_keys()) + 2):
        cred = kp.pick()  # raises RateLimited when every credential is resting
        q = dict(params or {})
        if mail:
            q["mailto"] = mail
        if cred:
            q["api_key"] = cred
        url = OPENALEX + path + ("?" + urllib.parse.urlencode(q) if q else "")
        rotate = False
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    kp.record_success(cred, resp.headers)
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    kp.record_success(cred, e.headers)
                    return None
                if e.code in (401, 403) and cred:
                    kp.record_invalid(cred, e.code)
                    rotate = True
                    break
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After", "0") or 0)
                    if wait > 60:  # daily budget gone for this credential -> rotate now
                        kp.record_rate_limited(cred, wait)
                        rotate = True
                        break
                    time.sleep(max(wait, 2 * (attempt + 1)))  # short burst limit: brief backoff
                    continue
                print(f"[openalex] {path} -> HTTP {e.code}")
                return None
            except Exception as e:  # network errors
                print(f"[openalex] {path} failed: {e}")
                time.sleep(1)
        if not rotate:
            return None
    raise RateLimited(kp.status()["message"])


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


# Scholarly output only: datasets, supplementary files, figures, paratext etc. are excluded at the
# source (they filled whole 50-paper lists, e.g. "Data for EMSL Project ..." x50).
SCHOLARLY_TYPES = "article|review|book|book-chapter|preprint|report|dissertation|letter"


def fetch_author_works(author_id, from_year):
    # 100 candidates so that after de-duplication (pipe.clean_works) a full list of 50 remains
    return _results(_get_json("/works", {
        "filter": f"authorships.author.id:{short_id(author_id)},from_publication_date:{from_year}-01-01,type:{SCHOLARLY_TYPES}",
        "sort": "publication_date:desc",
        "per-page": "100",
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


BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
READER = "https://r.jina.ai/"   # public reader: renders the page (incl. bot checks / JS) and returns Markdown


def _blocked(text):
    t = (text or "")[:3000].lower()
    return ("just a moment" in t and "cloudflare" in t) or "enable javascript and cookies" in t or "attention required" in t


def _via_reader(url, patient=False):
    """Fallback for sites that block automated requests (e.g. most umich.edu pages return a
    Cloudflare 403). Returns Markdown with [text](url) links, the same shape html_to_text gives.
    patient=True asks the reader to wait for the page to render (up to 30 s), which gets past
    Cloudflare's "Just a moment..." check that the quick read sometimes returns."""
    # Plain request (like curl): the reader rejects some custom header combinations with 403.
    headers = {"User-Agent": "curl/8.5.0", "Accept": "*/*"}
    if patient:
        headers.update({"X-Timeout": "30", "X-Wait-For-Selector": "main"})
    req = urllib.request.Request(READER + url, headers=headers)
    with urllib.request.urlopen(req, timeout=90 if patient else 60) as resp:
        md = resp.read(5_000_000).decode("utf-8", errors="ignore")
    title = ""
    m = re.match(r"\s*Title:\s*(.+)", md)
    if m:
        title = m.group(1).strip()[:200]
    body = md.split("Markdown Content:", 1)[-1]
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", body)          # drop images
    body = re.sub(r"\[\[email[^\]]*\]\]\([^)]*\)", " ", body)  # drop obfuscated e-mail links
    return title, body


_PAGE_CACHE = {}
_PAGE_CACHE_LOCK = threading.Lock()


def fetch_page_cached(url, ttl=3600):
    """fetch_page with an in-memory cache (1 h): profile pages read during discovery are not
    downloaded again for the affiliation / hiring steps that follow."""
    now = time.time()
    with _PAGE_CACHE_LOCK:
        hit = _PAGE_CACHE.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    page = fetch_page(url)
    if page.get("ok"):
        with _PAGE_CACHE_LOCK:
            if len(_PAGE_CACHE) > 3000:
                _PAGE_CACHE.clear()
            _PAGE_CACHE[url] = (now, page)
    return page


def fetch_page(url):
    """Returns {"url", "title", "text", "ok", "via"}; never raises.
    Direct request first; if the site blocks bots, retry through the reader service."""
    out = {"url": url or "", "title": "", "text": "", "ok": False, "via": ""}
    if not url or not url.startswith("http"):
        return out
    blocked = False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml",
                                                   "Accept-Language": "en-US,en;q=0.9"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read(5_000_000).decode("utf-8", errors="ignore")
            final = resp.geturl()
        if "html" in ctype or "text" in ctype:
            if _blocked(raw):
                blocked = True
            else:
                m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
                text = html_to_text(raw, final)
                out.update(url=final, title=html.unescape(" ".join(m.group(1).split()))[:200] if m else "",
                           text=text, ok=len(text) > 50, via="direct")
    except urllib.error.HTTPError as e:
        blocked = e.code in (401, 403, 429, 503)
        if not blocked:
            print(f"[fetch] {url} -> HTTP {e.code}")
    except Exception as e:
        blocked = True
        print(f"[fetch] {url} failed: {e}")
    if blocked or not out["ok"]:
        for attempt in range(3):   # the reader service rate-limits bursts (HTTP 429)
            try:
                title, text = _via_reader(url)
                if len(text) > 200 and not _blocked(text):
                    out.update(url=url, title=title, text=text, ok=True, via="reader")
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    time.sleep(8 * (attempt + 1))
                    continue
                print(f"[fetch] reader for {url} failed: {e}")
                break
    if not out["ok"]:
        # last try: let the reader wait for the bot check to clear
        try:
            title, text = _via_reader(url, patient=True)
            if len(text) > 200 and not _blocked(text) and "Just a moment" not in title:
                out.update(url=url, title=title, text=text, ok=True, via="reader_patient")
        except Exception as e:
            print(f"[fetch] patient reader for {url} failed: {e}")
    return out


# ---------------- rule-based faculty extraction (no LLM needed) ----------------

_TITLE_RE = re.compile(r"\b((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor\b[^\n\[\]]{0,80})", re.I)
_EXCLUDE_TITLE = re.compile(r"emerit|adjunct|affiliate|courtesy|visiting|lecturer|teaching|clinical|practice|research professor|professor of practice", re.I)
_LINK_RE = re.compile(r"\[([^\]]{3,160})\]\((https?://[^)\s]+)\)")
_BAD_NAME = re.compile(r"faculty|directory|people|department|school|college|university|research|news|events|about|contact|staff|students|home|program|center|lab\b|search|filter|view|profile|more|apply|give|login|professor|engineering|science|medicine|robotics|mathematics|physics|chemistry|biology|institute|interests|office|phone|email|website|mentoring|plan\b|policy|guidelines|resources|handbook", re.I)
_PROFILE_BLOCKED_HOSTS = ("x.com", "twitter.com", "linkedin.com", "facebook.com", "instagram.com", "youtube.com")


def _clean_name(s):
    # One canonical cleaner for every extraction path (rules, WordPress, sitemap profiles).
    from services import name_utils as nu
    return nu.clean_person_name(s)


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


_DEPT_WORDS = re.compile(r"engineering|science|sciences|mathemat|statistic|physics|chemistry|biology|economics|psychology|computing|informatics|information|robotics|medicine|design|studies", re.I)


def _dept_near(text, end):
    """On college-wide directories the department is a link right after the title
    (e.g. MSU: '[Name](..) Associate Professor [Biomedical Engineering](.../departments/bme)')."""
    after = text[end:end + 260]
    for m in _LINK_RE.finditer(after):
        label = " ".join(m.group(1).split())
        if 3 <= len(label) <= 80 and _DEPT_WORDS.search(label) and not re.search(r"@|\d{3}", label):
            return label
        break  # only the first link after the title is considered
    return ""


def _flip(name):
    """"Ackerman, Mark S." -> "Mark S. Ackerman" (directories often list Last, First)."""
    if name.count(",") == 1:
        last, first = [x.strip() for x in name.split(",")]
        if last and first and len(last.split()) <= 2 and not re.search(r"ph\.?d|jr|sr|iii", first, re.I):
            return f"{first} {last}".strip()
    return name


def _line_faculty(text, department):
    """Unlinked layout: a name on its own line, the title on the next line
    (e.g. UMich CSE: "Adler, Dan" / "Assistant Professor, EECS- Computer Science and Engineering")."""
    lines = [l.strip() for l in text.split("\n")]
    out = []
    for i in range(len(lines) - 1):
        cand = lines[i]
        if not cand or len(cand) > 60 or "[" in cand or "http" in cand:
            continue
        nxt = ""
        for j in range(i + 1, min(i + 3, len(lines))):
            if lines[j]:
                nxt = lines[j]
                break
        m = _TITLE_RE.search(nxt)
        # the professor rank must appear in the first title on the line (named chairs first:
        # "S. Jack Hu Collegiate Professor of ... Professor, EECS"), and not as research/emeritus
        if not m or m.start() > 90 or _EXCLUDE_TITLE.search(nxt[:m.end() + 20]):
            continue
        name = _clean_name(cand)
        if _looks_like_name(name):
            out.append((name, m.group(1), ""))
    return out


def extract_faculty_rules(page, department):
    """Pull professor-rank faculty from an official directory page: linked names + nearby titles.

    `department` is the directory's label. For college-wide directories (label starting with
    "College of"/"School of") the per-person department link is used when present.
    Handles: "[Name](url) Title", "[Name Title](url)", "[Last, First](url)" and unlinked
    "Name" / "Title" line pairs. Titles such as "Research Professor", "Emeritus", adjunct,
    lecturer, clinical and visiting are excluded; people whose FIRST listed title is a
    professor rank are kept.
    """
    if not page.get("ok"):
        return []
    text = page["text"]
    from services import store as st
    college_wide = bool(re.match(r"(college|school|faculty) of", department or "", re.I))
    generic_dept = not st.clean_department(department or "")
    out, seen = [], set()
    for name, title, _ in _line_faculty(text, department):
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rank = re.match(r"((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor)", title, re.I)
        out.append({"name": name, "title": (rank.group(1) if rank else title)[:80], "department": department, "profile_url": ""})
    for m in _LINK_RE.finditer(text):
        label, url = m.group(1).strip(), m.group(2)
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(h == host or host.endswith("." + h) for h in _PROFILE_BLOCKED_HOSTS):
            continue
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
            # the line-pair pass found this person without a profile link; add the link
            for o in out:
                if o["name"].lower() == key and not o["profile_url"]:
                    o["profile_url"] = url
            continue
        seen.add(key)
        # Keep the rank only (drop trailing campus names etc.)
        rank = re.match(r"((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor)", title, re.I)
        dept = department
        if college_wide or generic_dept:
            dept = _dept_near(text, m.end()) or department
        out.append({"name": name, "title": (rank.group(1) if rank else title)[:80],
                    "department": dept, "profile_url": url})
    return out


def _get_json_any(url):
    """JSON from a university site: direct first, reader fallback when blocked."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        pass
    req = urllib.request.Request(READER + url, headers={"User-Agent": "curl/8.5.0", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    start = min([i for i in (body.find("["), body.find("{")) if i >= 0] or [0])
    return json.loads(body[start:])


def wordpress_faculty(site_url, department):
    """Many department sites are WordPress with a public "people" feed carrying each person's
    official primary title (e.g. every *.engin.umich.edu site). Returns the same rows as
    extract_faculty_rules, or [] when the site has no such feed."""
    parts = urllib.parse.urlparse(site_url)
    base = f"{parts.scheme}://{parts.netloc}"
    out, seen = [], set()
    for page in range(1, 11):
        url = (f"{base}/wp-json/wp/v2/people?per_page=100&page={page}"
               "&_fields=title,link,meta.umcoecm_people_primary_title,meta.umcoecm_people_sort_key")
        data = None
        for attempt in range(3):   # the reader service rate-limits bursts (HTTP 429)
            try:
                data = _get_json_any(url)
                break
            except Exception as e:
                if "429" in str(e):
                    time.sleep(8 * (attempt + 1))
                    continue
                break
        if data is None:
            break
        if not isinstance(data, list) or not data:
            break
        for p in data:
            name = html.unescape(((p.get("title") or {}).get("rendered") or "")).strip()
            title = ((p.get("meta") or {}).get("umcoecm_people_primary_title") or "").strip()
            m = _TITLE_RE.search(title)
            if not name or not m or m.start() > 60 or _EXCLUDE_TITLE.search(title[:m.end() + 20]):
                continue
            name = _clean_name(name)
            if not _looks_like_name(name) or name.lower() in seen:
                continue
            seen.add(name.lower())
            rank = re.match(r"((?:Distinguished |Endowed |University |Collegiate |Full |Associate |Assistant )*Professor)", m.group(1), re.I)
            out.append({"name": name, "title": (rank.group(1) if rank else m.group(1))[:80],
                        "department": department, "profile_url": p.get("link", "")})
        if len(data) < 100:
            break
    return out


def extract_faculty_any(url, department):
    """Everything we know how to read for one directory URL: page parser first, then the
    WordPress people feed when the page itself is rendered by JavaScript or only shows
    part of the list (many UMich engineering pages load people dynamically)."""
    page = fetch_page(url)
    rows = extract_faculty_rules(page, department)
    if len(rows) < 40:
        try:
            wp = wordpress_faculty(url, department)
            if len(wp) > len(rows):
                return wp, "wordpress"
        except Exception as e:
            print(f"[fetch] wordpress feed for {url} failed: {e}")
    return rows, page.get("via") or "fail"


_QUOTE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "`": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u00ad": "",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ", "\u200b": "",
})


def normalize_quote_text(t):
    """Formatting-only normalisation: HTML entities, curly quotes/apostrophes, dashes,
    non-breaking/zero-width spaces, line breaks and repeated whitespace. Wording, letters
    and punctuation order are untouched, so a paraphrase still fails."""
    t = html.unescape(t or "")
    t = re.sub(r"\*\*|__|\[|\]\([^)]*\)", "", t)   # markdown emphasis / link targets from the reader
    t = t.translate(_QUOTE_MAP)
    return " ".join(t.split()).strip().lower()


def quote_on_page(quote, page_text):
    """Strict verification: the normalised quote must appear verbatim in the normalised page.
    Trailing punctuation differences are tolerated; nothing fuzzy or semantic."""
    q = normalize_quote_text(quote).strip(" \"'")
    if len(q) < 20:
        return False
    body = normalize_quote_text(page_text)
    return q in body or q.rstrip(".!?;:") in body


def squash(t):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split())


def same_domain(url, website):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    base = urllib.parse.urlparse(website or "").netloc.lower().replace("www.", "")
    parts = base.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else base
    return bool(root) and host.endswith(root)
