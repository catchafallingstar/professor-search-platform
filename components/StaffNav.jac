import from "@jac/runtime" { Link, useLocation }

def:pub StaffNav() -> JsxElement {
    path = useLocation().pathname;
    base = "rounded-md pt-1.5 pb-1.5 pl-3 pr-3 text-sm font-medium transition-colors ";
    on = "bg-background shadow-sm";
    off = "text-muted-foreground hover:text-foreground";
    return <div className="flex flex-wrap items-center gap-3">
        <span className="rounded-full bg-primary/10 pt-0.5 pb-0.5 pl-2.5 pr-2.5 text-xs font-medium text-primary">{"Staff console"}</span>
        <div className="flex gap-1 rounded-lg bg-muted pt-1 pb-1 pl-1 pr-1">
            <Link to="/staff" className={base + (on if path == "/staff" else off)}>{"Pipeline"}</Link>
            <Link to="/staff/database" className={base + (on if path == "/staff/database" else off)}>{"Database"}</Link>
        </div>
    </div>;
}
