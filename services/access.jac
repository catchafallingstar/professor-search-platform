"""Roles.

* OWNER  - exactly one account: OWNER_EMAIL. Can grant and revoke staff.
* STAFF  - emails the owner granted. Can run the pipeline and see the database.
* USER   - everyone else (search + their own history).

Identity is NEVER taken from the browser. The caller's email is looked up server-side
from the verified login record that belongs to the caller's own root (services/identity.py),
so nobody can claim to be the owner or a staff member by sending an email string.
There is no "first account becomes staff" bootstrap any more.
"""

import os;
import secrets;
import services.store as st;
import from jaclang.server.identity.user_manager { UserManager }

glob OWNER_EMAIL: str = "bxybai@umich.edu";

node StaffRegistry {
    has root_ids: list[str] = [],   # legacy field, no longer trusted for access
        emails: list[str] = [];     # staff granted by the owner
}

obj WhoAmI {
    has logged_in: bool = False,
        email: str = "",
        is_staff: bool = False,
        is_owner: bool = False;
}

obj AccessResult {
    has ok: bool = True,
        message: str = "";
}

def _registry() -> StaffRegistry {
    found = [root.shared -->][?:StaffRegistry];
    if len(found) > 0 {
        return found[0];
    }
    reg = root.shared ++> StaffRegistry();
    grant(reg, level=WritePerm);
    return reg;
}

# Staff emails live in MongoDB (settings "staff_emails") so they survive sandbox resets.
def _staff() -> list[str] {
    try {
        return [str(x) for x in (st.get_setting("staff_emails", []) or [])];
    } except Exception {
        return [];
    }
}

def _set_staff(emails: list[str]) -> None {
    st.set_setting("staff_emails", emails);
}

def _is_guest() -> bool {
    return jid(root) == jid(root.shared);
}

def _norm_root_id(value: any) -> str {
    return str(value or "").replace("-", "").lower();
}

def caller_email() -> str {
    # Current Jac keeps login identities in its identity store, not the old
    # .jac/data/users.db SQLite file.
    if _is_guest() { return ""; }

    wanted: str = _norm_root_id(jid(root));
    if not wanted { return ""; }

    users = UserManager();
    offset: int = 0;
    page_size: int = 200;

    while True {
        page: list[dict[str, any]] = users.list_all_users(
            limit=page_size,
            offset=offset
        );

        if not page { break; }

        for user in page {
            if _norm_root_id(user.get("root_id")) != wanted {
                continue;
            }

            identities: list[dict[str, any]] = user.get("identities") or [];

            for kind in ["email", "username"] {
                for identity in identities {
                    if str(identity.get("type") or "") == kind {
                        return str(
                            identity.get("value_normalized")
                            or identity.get("value_raw")
                            or ""
                        ).strip().lower();
                    }
                }
            }

            return "";
        }

        if len(page) < page_size { break; }
        offset += len(page);
    }

    return "";
}

def:protect is_owner_caller() -> bool {
    e = caller_email();
    return bool(e) and e == OWNER_EMAIL;
}

def:protect is_staff_caller() -> bool {
    e = caller_email();
    if not e { return False; }
    return e == OWNER_EMAIL or e in _staff();
}

def:protect require_staff() -> None {
    if not is_staff_caller() {
        raise PermissionError("Staff only");
    }
}

def:protect require_owner() -> None {
    if not is_owner_caller() {
        raise PermissionError("Only the site owner can manage staff accounts");
    }
}

def:protect whoami(email: str = "") -> WhoAmI {
    # f"email" from the client is ignored for access decisions; kept only for API compatibility.
    _ = email;
    e = caller_email();
    return WhoAmI(
        logged_in=True,
        email=e,
        is_staff=bool(e) and (e == OWNER_EMAIL or e in _staff()),
        is_owner=bool(e) and e == OWNER_EMAIL
    );
}

def:protect promote_staff(email: str) -> AccessResult {
    require_owner();
    e = email.strip().lower();
    if not e or "@" not in e {
        return AccessResult(ok=False, message="Enter the account's email address.");
    }
    if e == OWNER_EMAIL {
        return AccessResult(message="That is the owner account; it always has staff access.");
    }
    cur = _staff();
    if e in cur {
        return AccessResult(message=f"{e} is already staff.");
    }
    _set_staff(cur + [e]);
    return AccessResult(message=f"{e} is now staff. It takes effect the next time they sign in or reload.");
}

def:protect revoke_staff(email: str) -> AccessResult {
    require_owner();
    e = email.strip().lower();
    cur = _staff();
    if e not in cur {
        return AccessResult(ok=False, message=f"{e} is not a staff account.");
    }
    _set_staff([x for x in cur if x != e]);
    return AccessResult(message=f"{e} no longer has staff access.");
}

def:protect list_staff() -> list[str] {
    require_staff();
    return [OWNER_EMAIL + " (owner)"] + [e for e in _staff() if e and e != OWNER_EMAIL];
}

def:protect reset_staff_registry() -> AccessResult {
    # Owner-only cleanup: drops every staff grant.
    require_owner();
    _set_staff([]);
    return AccessResult(message="All staff grants removed. Only the owner has staff access now.");
}

# ---- internal worker token (the background worker is not a logged-in user) ----

glob WORKER_TOKEN_FILE: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".worker_token");

def:protect worker_token() -> str {
    existing: str = "";
    if os.path.exists(WORKER_TOKEN_FILE) {
        with open(WORKER_TOKEN_FILE) as fh {
            existing = str(fh.read()).strip();
        }
    }
    if existing { return existing; }
    fresh: str = str(secrets.token_hex(24));
    with open(WORKER_TOKEN_FILE, "w") as fh {
        fh.write(fresh);
    }
    return fresh;
}

def:protect check_worker_token(token: str) -> bool {
    return bool(token) and secrets.compare_digest(token.encode(), worker_token().encode());
}
