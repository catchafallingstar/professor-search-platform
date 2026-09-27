"""Durable snapshot of the professor directory.

Why: the live graph lives in .jac/data (SQLite inside the preview sandbox). When the
sandbox is replaced that folder is wiped, so everything the pipeline collected vanished.
This module writes a compact JSON copy into the synced project folder (data/directory.json)
and the server reloads it on startup when the graph is empty.

The JSON is plain data (no Jac objects) so it survives schema changes:
{ "version": 1, "saved_at": "...", "institutions": [ { ...fields, "professors": [
    { ...fields, "anchors": [...], "papers": [ {...paper, "subfields": [..], "fields": [..]} ],
      "grants": [ {...grant, "role": ".."} ], "hiring": {...} } ] } ] }
"""

import json
import os
import tempfile
import threading
import time

# NOTE: in the JacHammer preview, files the server writes inside the sandbox are NOT synced
# back to the project, so data/ alone does not survive a sandbox replacement. For that, set
# DIRECTORY_SNAPSHOT_URL (Settings > Environment) to a storage endpoint you control; the
# snapshot is then also PUT there and fetched on startup. Without it, data/directory.json
# still survives normal preview restarts inside the same sandbox, and it ships with a deploy.
_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PATH = os.path.join(_DIR, "directory.json")
_LOCK = threading.Lock()
_LAST_SAVE = {"t": 0.0}


def _remote_url():
    return os.environ.get("DIRECTORY_SNAPSHOT_URL", "").strip()


# ---- MongoDB copy (DIRECTORY_MONGODB_URI) ----
# The app's own database stays local (Jac's built-in Mongo mode breaks guest requests in
# this release), and the whole directory is mirrored into MongoDB as compressed chunks:
# db "professor_atlas", collection "directory_snapshot". Survives any sandbox reset.
_CHUNK = 8 * 1024 * 1024


def _mongo_col():
    uri = os.environ.get("DIRECTORY_MONGODB_URI", "").strip()
    if not uri:
        return None
    try:
        from pymongo import MongoClient
        return MongoClient(uri, serverSelectionTimeoutMS=15000)["professor_atlas"]["directory_snapshot"]
    except Exception as e:
        print(f"[snapshot] mongo unavailable: {e}")
        return None


def _mongo_put(body):
    col = _mongo_col()
    if col is None:
        return
    try:
        import zlib
        data = zlib.compress(body, 6)
        parts = [data[i:i + _CHUNK] for i in range(0, len(data), _CHUNK)] or [b""]
        gen = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
        col.insert_many([{"gen": gen, "n": i, "of": len(parts), "data": p} for i, p in enumerate(parts)])
        col.delete_many({"gen": {"$ne": gen}})   # keep only the newest complete copy
    except Exception as e:
        print(f"[snapshot] mongo save failed: {e}")


def _mongo_get():
    col = _mongo_col()
    if col is None:
        return None
    try:
        import zlib
        head = col.find_one({"n": 0}, sort=[("gen", -1)])
        if not head:
            return None
        parts = list(col.find({"gen": head["gen"]}).sort("n", 1))
        if len(parts) != head["of"]:
            return None
        return json.loads(zlib.decompress(b"".join(p["data"] for p in parts)).decode("utf-8"))
    except Exception as e:
        print(f"[snapshot] mongo load failed: {e}")
        return None


def _remote_get():
    m = _mongo_get()
    if m is not None:
        return m
    url = _remote_url()
    if not url:
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[snapshot] remote load failed: {e}")
        return None


def _remote_put(body):
    _mongo_put(body)
    url = _remote_url()
    if not url:
        return
    try:
        import urllib.request
        req = urllib.request.Request(url, data=body, method="PUT", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        print(f"[snapshot] remote save failed: {e}")


def exists():
    return os.path.exists(PATH) and os.path.getsize(PATH) > 20


def load():
    try:
        with open(PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        pass
    return _remote_get()


def save(payload, force=False, min_interval=20.0):
    """Atomic write (tmp file + rename) so a crash never leaves a half-written snapshot.
    Throttled to at most once every `min_interval` seconds unless force=True."""
    now = time.time()
    if not force and now - _LAST_SAVE["t"] < min_interval:
        return False
    with _LOCK:
        os.makedirs(_DIR, exist_ok=True)
        payload = dict(payload)
        payload["version"] = 1
        payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=_DIR, prefix=".directory.", suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        os.replace(tmp, PATH)
        _LAST_SAVE["t"] = now
    _remote_put(body)
    return True


def info():
    if not exists():
        return {"exists": False, "saved_at": "", "institutions": 0, "professors": 0, "bytes": 0}
    d = load() or {}
    insts = d.get("institutions", [])
    return {
        "exists": True,
        "saved_at": d.get("saved_at", ""),
        "institutions": len(insts),
        "professors": sum(len(i.get("professors", [])) for i in insts),
        "bytes": os.path.getsize(PATH),
    }
