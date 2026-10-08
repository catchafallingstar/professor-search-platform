"""Session store shared app-wide: who is signed in and whether they are staff.

Module-level f"glob" state + a window event ("atlas-auth") so every component that
calls useAuth() re-renders when the session changes (sign in, sign out, refresh).
"""

import from react { useEffect }
import from "@jac/runtime" { jacIsLoggedIn, jacLogout }
import from ..services.access { whoami, WhoAmI }

glob _me: any = None,
     _ready: bool = False,
     _loading: bool = False,
     _error: str = "",
     _request: int = 0;

def _broadcast() -> None {
    window.dispatchEvent(new(CustomEvent, "atlas-auth", {"detail": {"ready": _ready}}));  # jac:ignore[E1030,E1053]
}

async def:pub refreshSession(email: str = "") -> any {
    # Ignore late replies from older requests, including replies after sign-out.
    _request = _request + 1;
    request_id: int = _request;
    _loading = True;
    _error = "";
    fetched: any = None;
    transport_failed: bool = False;
    if not jacIsLoggedIn() {
        _me = None;
        _ready = True;
        _loading = False;
        _broadcast();
        return None;
    }
    try {
        fetched = await whoami(email);
    } except Exception {
        # RPC failure is not proof that the token is invalid: preserve it for retry.
        transport_failed = True;
    }
    if request_id != _request {
        return None;
    }
    if not jacIsLoggedIn() {
        _me = None;
        _error = "";
    } elif fetched is not None and fetched.logged_in and fetched.email {
        _me = fetched;
    } else {
        _me = None;
        _error = ("Could not reach the account service. Retry session verification."
                  if transport_failed else
                  "Your account identity could not be verified. Please sign in again.");
    }
    _ready = True;
    _loading = False;
    _broadcast();
    return _me;
}

def:pub signOut() -> None {
    _request = _request + 1;
    jacLogout();
    _me = None;
    _ready = True;
    _loading = False;
    _error = "";
    _broadcast();
}

def:pub useAuth() -> dict[str, any] {
    has tick: int = 0;
    useEffect(
        lambda {
            handler = lambda (e: any) { tick = tick + 1; };
            window.addEventListener("atlas-auth", handler);
            if _ready {
                # Session resolved before this component subscribed: re-render once to pick it up.
                tick = tick + 1;
            } elif not _loading {
                _loading = True;
                refreshSession("");
            }
            return lambda { window.removeEventListener("atlas-auth", handler); };
        },
        []
    );
    return {"me": _me, "ready": _ready, "error": _error, "tick": tick};
}