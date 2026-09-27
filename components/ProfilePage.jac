import from "@jac/runtime" { Link, useParams }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" {
    ArrowRight01Icon, ArrowUpRight01Icon, SparklesIcon, File01Icon, Money03Icon, Task01Icon
}
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.badge { Badge }
import from .ui.button { Button }
import from .ui.separator { Separator }
import from .ui.skeleton { Skeleton }
import from .ui.spinner { Spinner }
sv import from ..services.directory { get_professor_profile, ProfessorDetail, PaperView, GrantView }
sv import from ..services.pipeline { run_pipeline_for_professor, PipelineResult }
sv import from ..services.history { record_view }
import from "@jac/runtime" { jacIsLoggedIn }
import from .AuthContext { useAuth }

def fmt_amount(amount: float, currency: str) -> str {
    whole = str(int(amount));
    groups: list[str] = [];
    while len(whole) > 3 {
        groups = [whole[len(whole) - 3:]] + groups;
        whole = whole[:len(whole) - 3];
    }
    text = ",".join([whole] + groups);
    if currency == "" or currency == "USD" {
        return "$" + text;
    }
    return text + " " + currency;
}

def years_label(start: str, end: str) -> str {
    s = start[:4] if start else "";
    e = end[:4] if end else "";
    if s and e { return s + "-" + e; }
    return s or e;
}

def:pub GrantRow(g: GrantView) -> JsxElement {
    yl = years_label(g.start_date, g.end_date);
    return <div className="flex flex-col gap-1.5 rounded-lg border bg-muted/30 pt-3 pb-3 pl-4 pr-4">
        {g.funder_name and <span className="text-xs font-medium text-muted-foreground">{g.funder_name}</span>}
        <span className="font-medium leading-snug">{g.title or "Untitled award"}</span>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
            {g.role and <span>{"Role: " + g.role.replace("_", "-")}</span>}
            {g.funder_award_id and <span>{"Award: " + g.funder_award_id}</span>}
            {yl and <span className="tabular-nums">{yl}</span>}
            {g.amount > 0 and <span className="tabular-nums">{fmt_amount(g.amount, g.currency)}</span>}
        </div>
        {g.url and <a href={g.url} target="_blank" rel="noreferrer" className="text-sm text-primary underline-offset-4 hover:underline">{"Award page"}</a>}
    </div>;
}

def:pub PaperRow(x: PaperView) -> JsxElement {
    return <div className="flex gap-4 border-b pt-3 pb-3 last:border-b-0">
        <span className="w-12 shrink-0 text-sm font-medium tabular-nums text-muted-foreground">{str(x.year) if x.year else ""}</span>
        <div className="flex min-w-0 flex-col gap-1">
            <span className="font-medium leading-snug">{x.title}</span>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                {x.source_name and <span>{x.source_name}</span>}
                {for s in x.subfields { <span key={s}>{"Subfield: " + s}</span> }}
                {x.url and <a href={x.url} target="_blank" rel="noreferrer" className="text-primary underline-offset-4 hover:underline">{("DOI " + x.doi) if x.doi else "Link"}</a>}
            </div>
        </div>
    </div>;
}

def:pub ProfilePage() -> JsxElement {
    params = useParams();
    pid: str = params.id;
    has prof: ProfessorDetail | None = None,
        loading: bool = True,
        running: bool = False,
        error: str = "",
        run_message: str = "";

    async def load() -> None {
        loading = True;
        error = "";
        try {
            # Check the fetched value, not f"prof": state updates land on the next render.
            fetched = await get_professor_profile(pid);
            prof = fetched;
            if fetched is None {
                error = "Professor not found.";
            } elif jacIsLoggedIn() {
                try { await record_view(pid, fetched.name, fetched.university, fetched.department); } except Exception {}
            }
        } except Exception as e {
            error = "Could not load this profile: " + str(e);
        }
        loading = False;
    }

    async def run_pipeline() -> None {
        running = True;
        run_message = "";
        try {
            res: PipelineResult = await run_pipeline_for_professor(pid);
            run_message = res.message;
            prof = await get_professor_profile(pid);
        } except Exception as e {
            run_message = "Pipeline failed: " + str(e);
        }
        running = False;
    }

    async can with entry { await load(); }

    auth = useAuth();
    who: any = auth["me"];
    is_staff: bool = bool(who and who.is_staff);

    back = <Link to="/" className="flex w-fit items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground">
        <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={2} className="size-4 rotate-180" />{"All professors"}
    </Link>;

    if loading {
        return <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6">
            {back}
            <Skeleton className="h-10 w-72" />
            <Skeleton className="h-4 w-96" />
            <div className="grid gap-6 lg:grid-cols-3">
                <Skeleton className="h-80 rounded-lg lg:col-span-2" />
                <Skeleton className="h-80 rounded-lg" />
            </div>
        </div>;
    }
    if error or prof is None {
        return <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 pt-8 pb-12 pl-6 pr-6">
            {back}
            <div className="flex items-center gap-3 text-sm text-destructive">
                <span>{error or "Professor not found."}</span>
                <Button variant="outline" size="sm" onClick={load}>{"Retry"}</Button>
            </div>
        </div>;
    }
    p: ProfessorDetail = prof;
    status_variant = "default" if p.match_status == "MATCHED" else ("destructive" if p.match_status == "UNRESOLVED" else "secondary");

    return <div className="mx-auto flex w-full max-w-5xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
        {back}
        <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex flex-col gap-1">
                <h1 className="text-3xl font-semibold tracking-tight">{p.name}</h1>
                <span className="text-base">{p.title}</span>
                <span className="text-sm text-muted-foreground">{p.department}</span>
                <span className="text-sm text-muted-foreground">{p.university}</span>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-sm">
                    {p.faculty_url and <a href={p.faculty_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-primary underline-offset-4 hover:underline">{"Faculty Page"}<HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="size-3.5" /></a>}
                    {p.lab_url and <a href={p.lab_url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-primary underline-offset-4 hover:underline">{"Lab Website"}<HugeiconsIcon icon={ArrowUpRight01Icon} strokeWidth={2} className="size-3.5" /></a>}
                </div>
            </div>
            {is_staff and <div className="flex flex-col items-end gap-2">
                <Button className="shadow-sm" onClick={run_pipeline} disabled={running}>
                    {running and <Spinner />}{"Running pipeline" if running else "Re-run pipeline (staff)"}
                </Button>
                {run_message and <span className="max-w-xs text-right text-xs text-muted-foreground">{run_message}</span>}
            </div>}
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
            <div className="flex flex-col gap-6 lg:col-span-2">
                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"Recent Paper Subfields"}</CardTitle>
                        <CardDescription>{"Distinct OpenAlex subfields across recent papers. Department comes from the university."}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {<div className="flex flex-wrap gap-2">
                            {for s in p.subfields { <Badge key={s} variant="secondary">{s}</Badge> }}
                        </div> if len(p.subfields) > 0 else <p className="text-sm text-muted-foreground">{"No subfields collected yet. Run the pipeline to pull recent papers."}</p>}
                    </CardContent>
                </Card>

                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"Recent Papers"}</CardTitle>
                        <CardDescription>{str(len(p.papers)) + (" paper" if len(p.papers) == 1 else " papers") + " from the last five years"}</CardDescription>
                    </CardHeader>
                    <CardContent>
                        {<div className="flex flex-col">
                            {for x in p.papers { <PaperRow key={x.id} x={x} /> }}
                        </div> if len(p.papers) > 0 else <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed pt-10 pb-10 text-center">
                            <HugeiconsIcon icon={File01Icon} strokeWidth={2} className="size-5 text-muted-foreground" />
                            <span className="text-sm font-medium">{"No recent papers stored"}</span>
                            <span className="text-xs text-muted-foreground">{"Papers appear once the professor is matched to OpenAlex."}</span>
                        </div>}
                    </CardContent>
                </Card>

                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"Grants"}</CardTitle>
                        <CardDescription>{"Only awards that list this professor as PI, Co-PI or investigator."}</CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        {for g in p.grants { <GrantRow key={g.id} g={g} /> }}
                        {len(p.grants) == 0 and <div className="flex items-center gap-2 text-sm text-muted-foreground">
                            <HugeiconsIcon icon={Money03Icon} strokeWidth={2} className="size-4" />{"No grants found where this professor is listed as an investigator."}
                        </div>}
                    </CardContent>
                </Card>
            </div>

            <div className="flex flex-col gap-6">
                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"Hiring"}</CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        {<div className="flex flex-col gap-3">
                            <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                                <HugeiconsIcon icon={SparklesIcon} strokeWidth={2} className="size-4" />
                            </div>
                            <blockquote className="border-l-2 border-primary pl-3 text-sm italic leading-relaxed">{"\"" + p.hiring_quote + "\""}</blockquote>
                            <a href={p.hiring_source_url} target="_blank" rel="noreferrer" className="text-sm text-primary underline-offset-4 hover:underline">{"Source: " + (p.hiring_source_title or p.hiring_source_url)}</a>
                        </div> if p.hiring_quote else <p className="text-sm text-muted-foreground">{"No current hiring statement found."}</p>}
                        {p.hiring_last_checked and <span className="text-xs text-muted-foreground">{"Last checked " + p.hiring_last_checked[:10]}</span>}
                    </CardContent>
                </Card>

                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"OpenAlex identity"}</CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3 text-sm">
                        <div className="flex items-center justify-between gap-2">
                            <span className="text-muted-foreground">{"Match status"}</span>
                            <Badge variant={status_variant}>{p.match_status}</Badge>
                        </div>
                        {p.openalex_author_id and <div className="flex items-center justify-between gap-2">
                            <span className="text-muted-foreground">{"Author ID"}</span>
                            <a href={"https://openalex.org/" + p.openalex_author_id} target="_blank" rel="noreferrer" className="font-mono text-xs text-primary hover:underline">{p.openalex_author_id}</a>
                        </div>}
                        {p.orcid and <div className="flex items-center justify-between gap-2">
                            <span className="text-muted-foreground">{"ORCID"}</span>
                            <a href={"https://orcid.org/" + p.orcid} target="_blank" rel="noreferrer" className="font-mono text-xs text-primary hover:underline">{p.orcid}</a>
                        </div>}
                        {p.match_method and <div className="flex items-center justify-between gap-2">
                            <span className="text-muted-foreground">{"Method"}</span>
                            <span className="text-xs font-medium">{p.match_method.replace("_", " ")}</span>
                        </div>}
                        {p.match_note and <p className="text-xs text-muted-foreground">{p.match_note}</p>}
                        <Separator />
                        <span className="text-xs font-medium">{"Faculty-page publications"}</span>
                        {for a in p.anchors {
                            <div key={a.title} className="flex items-start gap-2 text-xs">
                                <HugeiconsIcon icon={Task01Icon} strokeWidth={2} className={"mt-0.5 size-3.5 shrink-0 " + ("text-primary" if a.used_for_match else "text-muted-foreground")} />
                                <div className="flex flex-col gap-0.5">
                                    <span className="leading-snug">{a.title}</span>
                                    <span className="text-muted-foreground">{(str(a.year) if a.year else "") + (("  DOI " + a.doi) if a.doi else "") + ("  used for match" if a.used_for_match else "")}</span>
                                </div>
                            </div>
                        }}
                        {len(p.anchors) == 0 and <span className="text-xs text-muted-foreground">{"None listed on the faculty page; name + institution fallback applies."}</span>}
                    </CardContent>
                </Card>
            </div>
        </div>
    </div>;
}
