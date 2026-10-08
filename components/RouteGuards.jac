"""Client-side route guards. The server still enforces access (def:priv + require_staff)."""

import from "@jac/runtime" { Navigate, useLocation }
import from .ui.spinner { Spinner }
import from .AuthContext { useAuth, refreshSession }

def:pub GuardLoading() -> JsxElement {
    return <div className="flex min-h-[50vh] items-center justify-center"><Spinner /></div>;
}

def:pub AuthRetry(message: str) -> JsxElement {
    return <div className="mx-auto flex min-h-[50vh] max-w-md flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="text-sm text-destructive">{message}</p>
        <button type="button" onClick={lambda { refreshSession(); }}
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground">{"Retry session verification"}</button>
    </div>;
}

def:pub RequireUser(children: any = None) -> JsxElement {
    auth = useAuth();
    loc = useLocation();
    if not auth["ready"] { return <GuardLoading />; }
    if auth["error"] { return <AuthRetry message={auth["error"]} />; }
    if auth["me"] is None {
        return <Navigate to={"/login?next=" + loc.pathname} replace={True} />;
    }
    return <>{children}</>;
}

def:pub RequireStaff(children: any = None) -> JsxElement {
    auth = useAuth();
    loc = useLocation();
    if not auth["ready"] { return <GuardLoading />; }
    if auth["error"] { return <AuthRetry message={auth["error"]} />; }
    me: any = auth["me"];
    if me is None {
        return <Navigate to={"/login?next=" + loc.pathname} replace={True} />;
    }
    if not me.is_staff {
        return <Navigate to="/account?denied=staff" replace={True} />;
    }
    return <>{children}</>;
}
