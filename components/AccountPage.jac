import from "@jac/runtime" { Link, useNavigate, useLocation }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Search01Icon, Clock01Icon, UserIcon, Delete02Icon, ArrowRight01Icon }
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from .ui.badge { Badge }
import from .ui.skeleton { Skeleton }
import from .ui.alert { Alert, AlertTitle, AlertDescription }
import from .ui.empty { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription, EmptyContent }
import from .AuthContext { useAuth }
sv import from ..services.history {
    get_search_history, get_view_history, clear_search_history, clear_view_history,
    SearchHistoryItem, ViewHistoryItem
}

def search_label(s: SearchHistoryItem) -> str {
    parts: list[str] = [];
    if s.query { parts.append("\"" + s.query + "\""); }
    if s.university { parts.append(s.university); }
    if s.department { parts.append(s.department); }
    if s.subfield { parts.append(s.subfield); }
    if s.has_grant { parts.append("has grant"); }
    if s.has_hiring { parts.append("hiring quote"); }
    return " / ".join(parts) if len(parts) > 0 else "All professors";
}

def search_href(s: SearchHistoryItem) -> str {
    q = "/?q=" + encodeURIComponent(s.query) + "&university=" + encodeURIComponent(s.university)
        + "&department=" + encodeURIComponent(s.department) + "&subfield=" + encodeURIComponent(s.subfield);
    if s.has_grant { q = q + "&grant=1"; }
    if s.has_hiring { q = q + "&hiring=1"; }
    return q;
}

def when(ts: str) -> str {
    return ts[:10] + " " + ts[11:16] if len(ts) >= 16 else ts;
}

def:pub AccountPage() -> JsxElement {
    has searches: list[SearchHistoryItem] = [],
        views: list[ViewHistoryItem] = [],
        loading: bool = True,
        error: str = "";

    auth = useAuth();
    loc = useLocation();
    nav = useNavigate();
    me: any = auth["me"];
    denied = "denied=staff" in loc.search;

    async def load() -> None {
        loading = True;
        error = "";
        try {
            searches = await get_search_history();
            views = await get_view_history();
        } except Exception as e {
            error = "Could not load your history: " + str(e);
        }
        loading = False;
    }

    async def clear_searches() -> None {
        try { await clear_search_history(); searches = []; } except Exception as e { error = str(e); }
    }

    async def clear_views() -> None {
        try { await clear_view_history(); views = []; } except Exception as e { error = str(e); }
    }

    async can with entry { await load(); }

    return <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
        <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex items-center gap-3">
                <div className="flex size-11 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <HugeiconsIcon icon={UserIcon} strokeWidth={2} className="size-5" />
                </div>
                <div className="flex flex-col gap-0.5">
                    <h1 className="text-2xl font-semibold tracking-tight">{"My account"}</h1>
                    <p className="text-sm text-muted-foreground">{(me.email if me else "") + (" - Staff" if me and me.is_staff else "")}</p>
                </div>
            </div>
            <div className="flex gap-2">
                {me and me.is_staff and <Button variant="outline" onClick={lambda { nav("/staff"); }}>{"Open staff console"}</Button>}
                <Button onClick={lambda { nav("/"); }}><HugeiconsIcon icon={Search01Icon} strokeWidth={2} />{"New search"}</Button>
            </div>
        </div>

        {denied and <Alert>
            <AlertTitle>{"Staff access required"}</AlertTitle>
            <AlertDescription>{"The ingestion pipeline and database are for staff accounts. Ask a staff member to add your email."}</AlertDescription>
        </Alert>}
        {error and <div className="flex items-center gap-3 text-sm text-destructive">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={load}>{"Retry"}</Button>
        </div>}

        <div className="grid gap-6 lg:grid-cols-2">
            <Card className="shadow-sm">
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                    <div className="flex flex-col gap-1">
                        <CardTitle>{"Search history"}</CardTitle>
                        <CardDescription>{"Click a search to run it again."}</CardDescription>
                    </div>
                    {len(searches) > 0 and <Button variant="ghost" size="sm" onClick={clear_searches}><HugeiconsIcon icon={Delete02Icon} strokeWidth={2} />{"Clear"}</Button>}
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                    {loading and <div className="flex flex-col gap-2"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
                    {not loading and len(searches) == 0 and <Empty className="border border-dashed pt-10 pb-10">
                        <EmptyHeader>
                            <EmptyMedia variant="icon"><HugeiconsIcon icon={Search01Icon} strokeWidth={2} /></EmptyMedia>
                            <EmptyTitle>{"No searches yet"}</EmptyTitle>
                            <EmptyDescription>{"Searches you run while signed in are saved here."}</EmptyDescription>
                        </EmptyHeader>
                        <EmptyContent><Button onClick={lambda { nav("/"); }}>{"Search professors"}</Button></EmptyContent>
                    </Empty>}
                    {not loading and <div className="flex flex-col divide-y">
                        {for s in searches[:50] {
                            <Link key={s.id} to={search_href(s)} className="group flex items-center justify-between gap-3 pt-2.5 pb-2.5 transition-colors hover:text-primary">
                                <div className="flex min-w-0 flex-col gap-0.5">
                                    <span className="truncate text-sm font-medium">{search_label(s)}</span>
                                    <span className="text-xs text-muted-foreground tabular-nums">{when(s.created_at) + " - " + str(s.result_count) + (" result" if s.result_count == 1 else " results")}</span>
                                </div>
                                <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={2} className="size-4 shrink-0 text-muted-foreground group-hover:text-primary" />
                            </Link>
                        }}
                    </div>}
                </CardContent>
            </Card>

            <Card className="shadow-sm">
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                    <div className="flex flex-col gap-1">
                        <CardTitle>{"Viewed professors"}</CardTitle>
                        <CardDescription>{"Profiles you opened, most recent first."}</CardDescription>
                    </div>
                    {len(views) > 0 and <Button variant="ghost" size="sm" onClick={clear_views}><HugeiconsIcon icon={Delete02Icon} strokeWidth={2} />{"Clear"}</Button>}
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                    {loading and <div className="flex flex-col gap-2"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div>}
                    {not loading and len(views) == 0 and <Empty className="border border-dashed pt-10 pb-10">
                        <EmptyHeader>
                            <EmptyMedia variant="icon"><HugeiconsIcon icon={Clock01Icon} strokeWidth={2} /></EmptyMedia>
                            <EmptyTitle>{"No professors viewed yet"}</EmptyTitle>
                            <EmptyDescription>{"Open a professor's profile and it shows up here."}</EmptyDescription>
                        </EmptyHeader>
                    </Empty>}
                    {not loading and <div className="flex flex-col divide-y">
                        {for v in views[:50] {
                            <Link key={v.id} to={"/professors/" + v.professor_id} className="group flex items-center justify-between gap-3 pt-2.5 pb-2.5 transition-colors hover:text-primary">
                                <div className="flex min-w-0 flex-col gap-0.5">
                                    <span className="truncate text-sm font-medium">{v.professor_name}</span>
                                    <span className="truncate text-xs text-muted-foreground">{v.university + (" - " + v.department if v.department else "")}</span>
                                </div>
                                <div className="flex shrink-0 items-center gap-2">
                                    {v.view_count > 1 and <Badge variant="secondary">{str(v.view_count) + " views"}</Badge>}
                                    <span className="text-xs text-muted-foreground tabular-nums">{when(v.viewed_at)}</span>
                                </div>
                            </Link>
                        }}
                    </div>}
                </CardContent>
            </Card>
        </div>
    </div>;
}
