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
import services.identity as ident;

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

def _is_guest() -> bool {
    return jid(root) == jid(root.shared);
}

def caller_email() -> str {
    # Verified login identity of whoever is making this request ("" for guests).
    if _is_guest() { return ""; }
    return str(ident.email_for_root(jid(root)));
}

def is_owner_caller() -> bool {
    e = caller_email();
    return bool(e) and e == OWNER_EMAIL;
}

def is_staff_caller() -> bool {
    e = caller_email();
    if not e { return False; }
    return e == OWNER_EMAIL or e in _registry().emails;
}

def require_staff() -> None {
    if not is_staff_caller() {
        raise PermissionError("Staff only");
    }
}

def require_owner() -> None {
    if not is_owner_caller() {
        raise PermissionError("Only the site owner can manage staff accounts");
    }
}

def:priv whoami(email: str = "") -> WhoAmI {
    # f"email" from the client is ignored for access decisions; kept only for API compatibility.
    _ = email;
    e = caller_email();
    return WhoAmI(
        logged_in=True,
        email=e,
        is_staff=bool(e) and (e == OWNER_EMAIL or e in _registry().emails),
        is_owner=bool(e) and e == OWNER_EMAIL
    );
}

def:priv promote_staff(email: str) -> AccessResult {
    require_owner();
    e = email.strip().lower();
    if not e or "@" not in e {
        return AccessResult(ok=False, message="Enter the account's email address.");
    }
    if e == OWNER_EMAIL {
        return AccessResult(message="That is the owner account; it always has staff access.");
    }
    reg = _registry();
    if e in reg.emails {
        return AccessResult(message=f"{e} is already staff.");
    }
    reg.emails = reg.emails + [e];
    return AccessResult(message=f"{e} is now staff. It takes effect the next time they sign in or reload.");
}

def:priv revoke_staff(email: str) -> AccessResult {
    require_owner();
    e = email.strip().lower();
    reg = _registry();
    if e not in reg.emails {
        return AccessResult(ok=False, message=f"{e} is not a staff account.");
    }
    reg.emails = [x for x in reg.emails if x != e];
    return AccessResult(message=f"{e} no longer has staff access.");
}

def:priv list_staff() -> list[str] {
    require_staff();
    return [OWNER_EMAIL + " (owner)"] + [e for e in _registry().emails if e and e != OWNER_EMAIL];
}

def:priv reset_staff_registry() -> AccessResult {
    # Owner-only cleanup: drops every staff grant (including any created by the old bootstrap).
    require_owner();
    reg = _registry();
    reg.emails = [];
    reg.root_ids = [];
    return AccessResult(message="All staff grants removed. Only the owner has staff access now.");
}

# ---- internal worker token (the background worker is not a logged-in user) ----

glob WORKER_TOKEN_FILE: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".worker_token");

def worker_token() -> str {
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

def check_worker_token(token: str) -> bool {
    return bool(token) and secrets.compare_digest(token.encode(), worker_token().encode());
}
