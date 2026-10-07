"""Background runner for the one-time MongoDB data upgrade.

JacHammer does not expose an interactive shell, and the migration can touch thousands of
MongoDB rows. Running it in a daemon thread lets the Staff page start it without holding one
HTTP request open until the whole migration finishes.
"""

import threading

from services import store as st

STATUS_KEY = "upgrade_safe_20261007_status"
DONE_KEY = "upgrade_safe_20261007_done"
STARTED_KEY = "upgrade_safe_20261007_started_at"
RAN_AT_KEY = "upgrade_safe_20261007_ran_at"
ERROR_KEY = "upgrade_safe_20261007_error"

_LOCK = threading.Lock()
_THREAD = {"thread": None}


def state():
    status = str(st.get_setting(STATUS_KEY, "PENDING") or "PENDING")
    return {
        "status": status,
        "done": bool(st.get_setting(DONE_KEY, False)),
        "started_at": str(st.get_setting(STARTED_KEY, "") or ""),
        "ran_at": str(st.get_setting(RAN_AT_KEY, "") or ""),
        "error": str(st.get_setting(ERROR_KEY, "") or ""),
    }


def _run():
    try:
        from services import cleanup
        cleanup.upgrade_safe()
        ran_at = st.now_iso()
        st.set_setting(DONE_KEY, True)
        st.set_setting(RAN_AT_KEY, ran_at)
        st.set_setting(ERROR_KEY, "")
        st.set_setting(STATUS_KEY, "DONE")
    except Exception as exc:
        st.set_setting(ERROR_KEY, f"{type(exc).__name__}: {str(exc)[:500]}")
        st.set_setting(STATUS_KEY, "FAILED")


def start():
    """Start the migration once. Returns current state immediately."""
    with _LOCK:
        cur = state()
        if cur["done"]:
            return cur
        t = _THREAD.get("thread")
        if t is not None and t.is_alive():
            return cur
        # If a prior preview/server stopped mid-migration, upgrade_safe is idempotent; restarting
        # it is safer than leaving the database permanently stuck in RUNNING.
        st.set_setting(STATUS_KEY, "RUNNING")
        st.set_setting(STARTED_KEY, st.now_iso())
        st.set_setting(ERROR_KEY, "")
        t = threading.Thread(target=_run, name="professor-atlas-db-upgrade", daemon=True)
        _THREAD["thread"] = t
        t.start()
        return state()
