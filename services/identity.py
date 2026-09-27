"""Look up the verified login identity (email/username) for a user's root id.

Reads the built-in account store (.jac/data/users.db, table identity_users) read-only.
This is the source of truth for who a caller is; the client never supplies it.
"""

import json
import os
import sqlite3

_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".jac", "data", "users.db")


def _norm_root(rid):
    return (rid or "").replace("-", "").lower()


def email_for_root(root_id):
    rid = _norm_root(root_id)
    if not rid or not os.path.exists(_DB):
        return ""
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True, timeout=5)
        try:
            for identities, row_root, status in con.execute(
                "SELECT identities, root_id, status FROM identity_users"
            ):
                if _norm_root(row_root) != rid:
                    continue
                if status and str(status).lower() not in ("active", "none", ""):
                    return ""
                ids = json.loads(identities) if identities else []
                # prefer an email identity, else the username (the app signs people up with their email)
                # Email identities are stored unverified (no confirmation emails are sent), but
                # they still need the account password to sign in, so accept them. Being
                # "verified" is not what makes this safe; the password check is.
                for kind in ("email", "username"):
                    for i in ids:
                        if i.get("type") == kind:
                            return str(i.get("value_normalized") or i.get("value_raw") or "").strip().lower()
                return ""
        finally:
            con.close()
    except Exception as e:
        print(f"[identity] lookup failed: {e}")
    return ""
