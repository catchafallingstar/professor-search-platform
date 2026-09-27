"""Client-side route guards. The server still enforces access (def:priv + require_staff)."""

import from "@jac/runtime" { Navigate, useLocation }
import from .ui.spinner { Spinner }
import from .AuthContext { useAuth }

def:pub GuardLoading() -> JsxElement {
    return <div className="flex min-h-[50vh] items-center justify-center"><Spinner /></div>;
}

def:pub RequireUser(children: any = None) -> JsxElement {
    auth = useAuth();
    loc = useLocation();
    if not auth["ready"] { return <GuardLoading />; }
    if auth["me"] is None {
        return <Navigate to={"/login?next=" + loc.pathname} replace={True} />;
    }
    return <>{children}</>;
}

def:pub RequireStaff(children: any = None) -> JsxElement {
    auth = useAuth();
    loc = useLocation();
    if not auth["ready"] { return <GuardLoading />; }
    me: any = auth["me"];
    if me is None {
        return <Navigate to={"/login?next=" + loc.pathname} replace={True} />;
    }
    if not me.is_staff {
        return <Navigate to="/account?denied=staff" replace={True} />;
    }
    return <>{children}</>;
}
