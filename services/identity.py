"""Resolve a signed-in Jac root to its account identity.

Jac 0.37+ stores accounts in its Postgres-backed UserManager, not
the legacy .jac/data/users.db SQLite table.  Never trust a client-submitted
email when evaluating OWNER/STAFF privileges.
"""

def _norm_root(value):
    return str(value or "").replace("-", "").lower()


def email_for_root(root_id):
    """Return the account email/username belonging to this authenticated root.

    Uses the public Jac UserManager API, with pagination to cover more than
    the first 100 accounts. Return empty on storage failures (fail closed).
    """
    wanted = _norm_root(root_id)
    if not wanted:
        return ""

    try:
        from jaclang.server.identity.user_manager import UserManager

        manager = UserManager()
        page_size = 200
        offset = 0
        while True:
            users = manager.list_all_users(limit=page_size, offset=offset)
            for user in users:
                if _norm_root(user.get("root_id")) != wanted:
                    continue
                if str(user.get("status") or "active").lower() != "active":
                    return ""
                identities = user.get("identities") or []
                for kind in ("email", "username"):
                    for identity in identities:
                        if identity.get("type") != kind:
                            continue
                        value = str(
                            identity.get("value_normalized")
                            or identity.get("value_raw")
                            or ""
                        ).strip().lower()
                        if value:
                            return value
                return ""
            if len(users) < page_size:
                break
            offset += page_size
    except Exception as exc:
        # An auth-storage failure must never grant staff permissions.
        print(f"[identity] caller identity lookup failed: {type(exc).__name__}: {exc}")
    return ""
