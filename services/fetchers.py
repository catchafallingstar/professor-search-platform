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


def _worker_loop():
    base = None
    time.sleep(5)
    while WORKER["running"]:
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
    """Recreate the owner login after a sandbox reset wiped the account store.

    Needs OWNER_PASSWORD (Settings > Environment). Registering an account that already
    exists simply fails, so this is safe to run on every boot."""
    email = os.environ.get("OWNER_EMAIL", "bxybai@umich.edu").strip().lower()
    password = os.environ.get("OWNER_PASSWORD", "")
    base = _local_api()
    if not password or not base:
        if not password:
            print("[auth] OWNER_PASSWORD not set; the owner account is not auto-created.")
        return
    body = {"identities": [{"type": "email", "value": email}],
            "credential": {"type": "password", "password": password}}
    req = urllib.request.Request(base + "/user/register", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f"[auth] owner account {email} created.")
    except Exception as e:
        print(f"[auth] owner account already present or not created ({str(e)[:80]}).")


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
        except Exception as e:
            print(f"[auth] account restore skipped: {e}")
        try:
            ensure_owner_account()
        except Exception as e:
            print(f"[auth] owner bootstrap failed: {e}")
        threading.Thread(target=_accounts_backup_loop, daemon=True).start()
        autostart = os.environ.get("PIPELINE_AUTOSTART", "1").strip() not in ("0", "false", "no")
        actions = ("restore", "reindex", "load_priority") if autostart else ("restore", "reindex")
        for action in actions:
            for attempt in range(12):   # retry ~2 min: early calls can 500 while the server warms up
                try:
                    r = call_internal("internal_action", {"action": action, "arg": ""}, timeout=900)
                    print(f"[persist] {action}: {(r or {}).get('message', '')}")
                    break
                except Exception as e:
                    if attempt == 11:
                        print(f"[persist] {action} failed after retries: {e}")
                    time.sleep(10)
        if autostart:
            start_worker()
        else:
            resume_if_flagged()
    threading.Thread(target=_go, daemon=True).start()


def resume_if_flagged():
    if os.path.exists(_RUN_FLAG) and not WORKER["running"]:
        start_worker()


def worker_running():
    return WORKER["running"]


def llm_configured():
    """True when an LLM key is available (needed for faculty-page extraction and hiring research)."""
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


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


def extract_faculty_rules(page, department):
    """Pull professor-rank faculty from an official directory page: linked names + nearby titles.

    `department` is the directory's label. For college-wide directories (label starting with
    "College of"/"School of") the per-person department link is used when present.
    """
    if not page.get("ok"):
        return []
    text = page["text"]
    college_wide = bool(re.match(r"(college|school|faculty) of", department or "", re.I))
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
        dept = department
        if college_wide:
            dept = _dept_near(text, m.end()) or department
        out.append({"name": name, "title": (rank.group(1) if rank else title)[:80],
                    "department": dept, "profile_url": url})
    return out


def squash(t):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split())


def same_domain(url, website):
    host = urllib.parse.urlparse(url or "").netloc.lower()
    base = urllib.parse.urlparse(website or "").netloc.lower().replace("www.", "")
    parts = base.split(".")
    root = ".".join(parts[-2:]) if len(parts) >= 2 else base
    return bool(root) and host.endswith(root)
