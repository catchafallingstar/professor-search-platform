"""OpenAlex API key pool with rotation and fallbacks.

Where keys come from (merged, de-duplicated, in this priority order):
  1. Keys added on the Pipeline page      -> stored in services/.openalex_keys (git-ignored)
  2. OPENALEX_API_KEYS  = "k1,k2,k3"      -> Settings > Environment (comma / space / newline separated)
  3. OPENALEX_API_KEY, OPENALEX_API_KEY_1 .. OPENALEX_API_KEY_20
  4. The keyless "anonymous" pool         -> always the LAST fallback

How a key is picked for each request:
  * Skip keys that are invalid (OpenAlex said 401/403) or cooling down (got a 429).
  * Among the rest, prefer the key with the MOST remaining daily budget, read from
    OpenAlex's X-RateLimit-Remaining header after every call. Ties -> least recently used.
    This spreads load so no single key is drained first.
  * Proactive switch: once a key's remaining budget drops below LOW_WATERMARK credits it is
    rested before OpenAlex ever returns 429, so the pipeline never stalls on a cooldown.
  * On 429 the key cools down for OpenAlex's Retry-After period; the request is retried
    immediately on the next key.
  * Cooldowns / invalid flags persist in services/.openalex_keystate.json, so a preview
    restart does not re-hammer a key that is still limited.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

OPENALEX = "https://api.openalex.org"
UA = "Mozilla/5.0 (ProfessorAtlas research crawler)"
ANON = ""                 # the keyless pool
LOW_WATERMARK = 30        # credits; below this a key is rested proactively (a search costs 10)
LOW_REST_SECONDS = 3600   # how long to rest a key that hit the watermark if OpenAlex gives no reset time

# Stored in <project>/data/ so they survive sandbox restarts (services/ dot-files did not).
_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(_DIR, exist_ok=True)
_KEY_FILE = os.path.join(_DIR, "openalex_keys.txt")
_STATE_FILE = os.path.join(_DIR, "openalex_keystate.json")
_LOCK = threading.Lock()


class RateLimited(Exception):
    """Every credential (all keys + the anonymous pool) is cooling down."""


# ---------------- persistent per-key state ----------------

def _load_state():
    try:
        with open(_STATE_FILE) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


_STATE = _load_state()    # key -> {cooldown_until, remaining, limit, invalid, uses, last_used, last_error}


def _save_state():
    try:
        with open(_STATE_FILE, "w") as fh:
            json.dump(_STATE, fh)
    except OSError:
        pass


def _st(cred):
    return _STATE.setdefault(cred or "__anon__", {
        "cooldown_until": 0.0, "remaining": None, "limit": None, "invalid": False,
        "uses": 0, "last_used": 0.0, "last_error": "",
    })


# ---------------- key sources ----------------

def _file_keys():
    try:
        with open(_KEY_FILE) as fh:
            return [ln.strip() for ln in fh if ln.strip()]
    except OSError:
        return []


def configured_keys():
    keys = list(_file_keys())
    keys += [k for k in re.split(r"[\s,;]+", os.environ.get("OPENALEX_API_KEYS", "")) if k]
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


def _source(key):
    if key in _file_keys():
        return "Pipeline page"
    return "Environment"


def mask(cred):
    return "No-key pool" if cred == ANON else f"...{cred[-4:]}"


# ---------------- selection ----------------

def _usable(cred, now):
    s = _st(cred)
    return not s["invalid"] and s["cooldown_until"] <= now


def pick():
    """Best usable credential, or raise RateLimited. Keys always beat the anonymous pool."""
    now = time.time()
    with _LOCK:
        keys = [k for k in configured_keys() if _usable(k, now)]
        if keys:
            # most remaining budget first (unknown = assume full), then least recently used
            def score(k):
                s = _st(k)
                rem = s["remaining"] if s["remaining"] is not None else 10 ** 9
                return (-rem, s["last_used"])
            return sorted(keys, key=score)[0]
        if _usable(ANON, now):
            return ANON
    raise RateLimited(status()["message"])


def _headers_int(headers, name):
    try:
        v = headers.get(name)
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def record_success(cred, headers):
    with _LOCK:
        s = _st(cred)
        s["uses"] += 1
        s["last_used"] = time.time()
        s["last_error"] = ""
        rem = _headers_int(headers, "X-RateLimit-Remaining")
        lim = _headers_int(headers, "X-RateLimit-Limit")
        if rem is not None:
            s["remaining"] = rem
        if lim is not None:
            s["limit"] = lim
        # proactive rotation: rest the key before OpenAlex starts refusing it
        if rem is not None and rem < LOW_WATERMARK:
            reset = _headers_int(headers, "X-RateLimit-Reset")
            s["cooldown_until"] = time.time() + (reset if reset and reset > 0 else LOW_REST_SECONDS)
            s["last_error"] = f"Rested early at {rem} credits left"
        _save_state()


def record_rate_limited(cred, retry_after):
    with _LOCK:
        s = _st(cred)
        s["cooldown_until"] = time.time() + max(int(retry_after or 0), 60)
        s["remaining"] = 0
        s["last_error"] = f"Rate-limited (resets in {max(int(retry_after or 0), 60) // 60} min)"
        _save_state()


def record_invalid(cred, code):
    with _LOCK:
        s = _st(cred)
        s["invalid"] = True
        s["last_error"] = f"Rejected by OpenAlex (HTTP {code})"
        _save_state()


# ---------------- management (used by the Pipeline page) ----------------

def _probe(key):
    """One cheap live call. Returns (ok, message, headers)."""
    url = OPENALEX + "/institutions?per-page=1" + (("&api_key=" + urllib.parse.quote(key)) if key else "")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
            return True, "ok", resp.headers
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "OpenAlex rejected this key.", e.headers
        if e.code == 429:
            return True, "rate-limited", e.headers
        return False, f"Could not verify key (HTTP {e.code}).", e.headers
    except Exception as e:
        return False, f"Could not reach OpenAlex: {e}", None


def add_key(key):
    key = (key or "").strip()
    if not key or re.search(r"\s", key):
        return {"ok": False, "message": "Paste one OpenAlex API key (no spaces)."}
    ok, msg, headers = _probe(key)
    if not ok:
        return {"ok": False, "message": msg}
    if key not in _file_keys() and key not in configured_keys():
        with open(_KEY_FILE, "a") as fh:
            fh.write(key + "\n")
    with _LOCK:
        s = _st(key)
        s["invalid"] = False
        s["last_error"] = ""
        if msg == "rate-limited":
            ra = _headers_int(headers, "Retry-After") or 3600
            s["cooldown_until"] = time.time() + ra
            s["remaining"] = 0
        else:
            s["cooldown_until"] = 0.0
            rem = _headers_int(headers, "X-RateLimit-Remaining")
            if rem is not None:
                s["remaining"] = rem
        _save_state()
    if msg == "rate-limited":
        return {"ok": True, "message": f"Key {mask(key)} added, but it is rate-limited right now. It joins the rotation when it resets."}
    return {"ok": True, "message": f"Key {mask(key)} added and verified. The worker uses it on its next step."}


def remove_key(masked):
    """Remove a Pipeline-page key by its masked id (...abcd). Environment keys are removed in Settings."""
    keys = _file_keys()
    keep = [k for k in keys if mask(k) != masked]
    if len(keep) == len(keys):
        return {"ok": False, "message": "That key comes from Settings > Environment; remove it there and restart the preview."}
    with open(_KEY_FILE, "w") as fh:
        fh.write("".join(k + "\n" for k in keep))
    return {"ok": True, "message": f"Key {masked} removed from the rotation."}


def reset_key(masked):
    """Clear the cooldown/invalid flag on a key (e.g. after fixing it)."""
    for k in configured_keys() + [ANON]:
        if mask(k) == masked:
            with _LOCK:
                s = _st(k)
                s["cooldown_until"] = 0.0
                s["invalid"] = False
                s["last_error"] = ""
                _save_state()
            return {"ok": True, "message": f"{masked} re-enabled."}
    return {"ok": False, "message": "Key not found."}


def key_rows():
    """Masked per-key status for the UI. Full keys never leave the server."""
    now = time.time()
    rows = []
    for i, k in enumerate(configured_keys() + [ANON]):
        s = _st(k)
        if s["invalid"]:
            state = "INVALID"
        elif s["cooldown_until"] > now:
            state = "COOLING_DOWN"
        else:
            state = "ACTIVE"
        rows.append({
            "id": mask(k),
            "source": "Built-in fallback" if k == ANON else _source(k),
            "state": state,
            "remaining": -1 if s["remaining"] is None else int(s["remaining"]),
            "limit": -1 if s["limit"] is None else int(s["limit"]),
            "cooldown_minutes": max(0, int((s["cooldown_until"] - now) // 60)),
            "uses": int(s["uses"]),
            "last_error": s["last_error"],
            "order": i,
        })
    return rows


def status():
    now = time.time()
    keys = configured_keys()
    usable = [c for c in keys + [ANON] if _usable(c, now)]
    if usable:
        return {"limited": False, "seconds_left": 0, "message": "",
                "keys_total": len(keys), "keys_available": len(usable)}
    cool = [_st(c)["cooldown_until"] for c in keys + [ANON] if not _st(c)["invalid"]]
    left = int(min(cool) - now) if cool else 0
    n = len(keys)
    msg = (f"All OpenAlex credentials are resting ({n} key{'s' if n != 1 else ''} + no-key pool); "
           f"the next one frees up in about {max(left, 0) // 60} min. Add another key on the Pipeline page to continue now.")
    return {"limited": True, "seconds_left": max(left, 0), "message": msg,
            "keys_total": n, "keys_available": 0}


def invalid_count():
    return sum(1 for k in configured_keys() if _st(k)["invalid"])
