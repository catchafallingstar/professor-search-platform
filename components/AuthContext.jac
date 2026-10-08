"""Session store shared app-wide: who is signed in and whether they are staff.

Module-level f"glob" state + a window event ("atlas-auth") so every component that
calls useAuth() re-renders when the session changes (sign in, sign out, refresh).
"""

import from react { useEffect }
import from "@jac/runtime" { jacIsLoggedIn, jacLogout }
import from ..services.access { whoami, WhoAmI }

glob _me: any = None,
     _ready: bool = False,
     _loading: bool = False;

def _broadcast() -> None {
    window.dispatchEvent(new(CustomEvent, "atlas-auth", {"detail": {"ready": _ready}}));  # jac:ignore[E1030,E1053]
}

async def:pub refreshSession(email: str = "") -> any {
    # Re-reads the session from the server. Returns the WhoAmI (or None when signed out).
    fetched: any = None;
    if not jacIsLoggedIn() {
        _me = None;
        _ready = True;
        _broadcast();
        return None;
    }
    try {
        fetched = await whoami(email);
    } except Exception {
        jacLogout();  # expired / invalid token -> signed out
        fetched = None;
    }
    _me = fetched;
    _ready = True;
    _broadcast();
    return fetched;
}

def:pub signOut() -> None {
    jacLogout();
    _me = None;
    _ready = True;
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
    return {"me": _me, "ready": _ready, "tick": tick};
}
