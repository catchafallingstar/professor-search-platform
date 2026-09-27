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
    uri = os.environ.get("DIRECTORY_MONGODB_URI", "").strip()
    if not uri:
        return None
    from pymongo import MongoClient
    return MongoClient(uri, serverSelectionTimeoutMS=15000)["professor_atlas"]["accounts"]


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
            added += 1
        con.commit()
    finally:
        con.close()
    return added
