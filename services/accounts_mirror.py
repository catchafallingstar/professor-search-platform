"""Mirror login accounts into MongoDB so they survive sandbox resets.

The server keeps accounts in .jac/data/users.db (SQLite). This copies the account rows
(email, hashed password, role) to MongoDB db "professor_atlas", collection "accounts",
and on boot re-inserts any account that is missing locally. Passwords stay hashed.
System accounts (admin / __guest__ / __system__) are never copied: the server makes them.
"""

import json
import os
import sqlite3

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".jac", "data", "users.db")
_SYSTEM = {"admin", "__guest__", "__system__"}
_COLS = ["user_id", "status", "identities", "credentials", "root_id", "role",
         "requires_password_reset", "profile", "created_at", "updated_at"]


def _col():
    # same cached, self-reconnecting client as the directory (picks up a changed password)
    from services import store as st
    if not st.configured():
        return None
    return st.db()["accounts"]


def _names(identities):
    try:
        return [str(i.get("value_normalized") or "") for i in json.loads(identities or "[]")]
    except ValueError:
        return []


def backup():
    """Copy every real (non-system) account to MongoDB. Returns how many were saved."""
    col = _col()
    if col is None or not os.path.exists(_DB):
        return 0
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=5)
    try:
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM identity_users").fetchall()
    finally:
        con.close()
    n = 0
    for row in rows:
        doc = dict(zip(_COLS, row))
        names = _names(doc["identities"])
        if not names or any(x in _SYSTEM for x in names):
            continue
        doc["_id"] = doc["user_id"]
        doc["lookups"] = names
        col.replace_one({"_id": doc["_id"]}, doc, upsert=True)
        n += 1
    return n


def restore():
    """Insert accounts from MongoDB that are missing locally. Returns how many were added."""
    col = _col()
    if col is None or not os.path.exists(_DB):
        return 0
    con = sqlite3.connect(_DB, timeout=10)
    added = 0
    try:
        taken = {r[0] for r in con.execute("SELECT value_normalized FROM identity_lookups")}
        for doc in col.find():
            names = doc.get("lookups") or _names(doc.get("identities"))
            if not names or any(x in taken for x in names):
                continue   # already exists (or a same-named account was registered here)
            con.execute(
                f"INSERT OR IGNORE INTO identity_users ({', '.join(_COLS)}) VALUES ({', '.join('?' * len(_COLS))})",
                [doc.get(c) for c in _COLS],
            )
            for name in names:
                con.execute("INSERT OR IGNORE INTO identity_lookups (value_normalized, user_id) VALUES (?, ?)",
                            (name, doc["user_id"]))
            _ensure_root(doc.get("root_id"))
            added += 1
        con.commit()
    finally:
        con.close()
    return added


_GRAPH = os.path.join(os.path.dirname(_DB), "anchor_store.db")


def _ensure_root(root_id):
    """A restored account points at its user root (graph node). After a sandbox reset that
    node is gone, the server logs "Invalid user_root_id" and every signed-in request 500s.
    Recreate an empty Root with the same id (the user's history starts fresh)."""
    rid = (root_id or "").replace("-", "").lower()
    if len(rid) != 32 or not os.path.exists(_GRAPH):
        return
    uid = f"{rid[0:8]}-{rid[8:12]}-{rid[12:16]}-{rid[16:20]}-{rid[20:]}"
    import datetime
    g = sqlite3.connect(_GRAPH, timeout=30)
    try:
        tmpl = g.execute("SELECT fingerprint, format_version FROM anchors WHERE arch_type='Root' LIMIT 1").fetchone()
        if tmpl is None or g.execute("SELECT 1 FROM anchors WHERE id=?", (uid,)).fetchone():
            return
        data = {"__type__": "NodeAnchor", "__module__": "jaclang.jac0core.archetype", "id": uid,
                "root": None, "persistent": True, "access": {"all": -1, "roots": {"anchors": {}}},
                "archetype": {"__type__": "Root", "__module__": "jaclang.jac0core.archetype"},
                "edges": [], "version": 0}
        g.execute("INSERT INTO anchors (id, type, arch_module, arch_type, fingerprint, data, format_version, updated_at) "
                  "VALUES (?, 'NodeAnchor', 'jaclang.jac0core.archetype', 'Root', ?, ?, ?, ?)",
                  (uid, tmpl[0], json.dumps(data), tmpl[1], datetime.datetime.now(datetime.timezone.utc).isoformat()))
        g.commit()
    finally:
        g.close()


def repair_roots():
    """Give every local account whose root node is missing a fresh one. Returns how many."""
    if not os.path.exists(_DB) or not os.path.exists(_GRAPH):
        return 0
    con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=5)
    try:
        rids = [r[0] for r in con.execute("SELECT root_id FROM identity_users")]
    finally:
        con.close()
    g = sqlite3.connect(f"file:{_GRAPH}?mode=ro", uri=True, timeout=5)
    try:
        have = {r[0].replace("-", "") for r in g.execute("SELECT id FROM anchors WHERE arch_type='Root'")}
    finally:
        g.close()
    n = 0
    for rid in rids:
        if rid and rid.replace("-", "").lower() not in have:
            _ensure_root(rid)
            n += 1
    return n
