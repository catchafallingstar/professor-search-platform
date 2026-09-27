"""Roles: every account is a USER; STAFF is an allow-list kept on the shared graph.

* The first account that asks f"whoami" while the staff list is empty becomes staff (bootstrap).
* Staff can promote other accounts by email.
* Every staff endpoint calls f"require_staff()" server-side, so hiding links in the UI is not the only guard.
"""

import os;
import secrets;

node StaffRegistry {
    has root_ids: list[str] = [],
        emails: list[str] = [];
}

node Account {
    # Lives on each user's own root; remembers the email they signed up with.
    has email: str = "";
}

obj WhoAmI {
    has logged_in: bool = False,
        email: str = "",
        is_staff: bool = False;
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

def _my_root_id() -> str {
    return jid(root);
}

def _is_guest() -> bool {
    return jid(root) == jid(root.shared);
}

def is_staff_caller() -> bool {
    if _is_guest() { return False; }
    return _my_root_id() in _registry().root_ids;
}

def require_staff() -> None {
    if not is_staff_caller() {
        raise PermissionError("Staff only");
    }
}

def _my_account() -> Account | None {
    acc = [root -->][?:Account];
    return acc[0] if len(acc) > 0 else None;
}

def:priv whoami(email: str = "") -> WhoAmI {
    # Called right after login with the email the user typed; stored on their own root once.
    acc = _my_account();
    if acc is None {
        acc = root ++> Account(email=email.strip().lower());
    } elif email and not acc.email {
        acc.email = email.strip().lower();
    }
    reg = _registry();
    rid = _my_root_id();
    if len(reg.root_ids) == 0 {
        reg.root_ids = [rid];
        reg.emails = [acc.email];
    } elif rid not in reg.root_ids and acc.email and acc.email in reg.emails {
        # promoted by email before this account first logged in
        reg.root_ids = reg.root_ids + [rid];
    }
    return WhoAmI(logged_in=True, email=acc.email, is_staff=rid in reg.root_ids);
}

def:priv promote_staff(email: str) -> AccessResult {
    require_staff();
    e = email.strip().lower();
    if not e or "@" not in e {
        return AccessResult(ok=False, message="Enter the account's email address.");
    }
    reg = _registry();
    if e in reg.emails {
        return AccessResult(message=f"{e} is already staff.");
    }
    reg.emails = reg.emails + [e];
    return AccessResult(message=f"{e} is now staff. It takes effect the next time they log in or reload.");
}

def:priv list_staff() -> list[str] {
    require_staff();
    return [e for e in _registry().emails if e];
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
