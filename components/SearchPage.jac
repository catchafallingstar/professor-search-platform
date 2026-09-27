import from "@jac/runtime" { Link }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Search01Icon, UserGroupIcon, SparklesIcon, Money03Icon }
import from .ui.card { Card, CardContent }
import from .ui.input { Input }
import from .ui.badge { Badge }
import from .ui.button { Button }
import from .ui.checkbox { Checkbox }
import from .ui.label { Label }
import from .ui.skeleton { Skeleton }
import from .ui.native_select { NativeSelect, NativeSelectOption }
import from .ui.empty { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription, EmptyContent }
sv import from ..services.directory {
    search_professors, get_filter_options, ProfessorSummary, FilterOptions
}
sv import from ..services.history { record_search }
import from "@jac/runtime" { useLocation, jacIsLoggedIn }

def:pub ResultCard(p: ProfessorSummary) -> JsxElement {
    grants_label = "1 grant" if p.grant_count == 1 else str(p.grant_count) + " grants";
    return <Link to={"/professors/" + p.id} className="block">
        <Card className="h-full shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md">
            <CardContent className="flex h-full flex-col gap-3">
                <div className="flex flex-col gap-0.5">
                    <span className="text-base font-semibold tracking-tight">{p.name}</span>
                    <span className="text-sm text-muted-foreground">{p.title}</span>
                </div>
                <div className="flex flex-col gap-0.5 text-sm">
                    <span className="font-medium">{p.university}</span>
                    <span className="text-muted-foreground">{p.department}</span>
                </div>
                <div className="flex flex-col gap-1.5">
                    <span className="text-xs text-muted-foreground">{"Recent subfields"}</span>
                    {<div className="flex flex-wrap gap-1.5">
                        {for s in p.subfields[:4] { <Badge key={s} variant="secondary">{s}</Badge> }}
                        {len(p.subfields) > 4 and <Badge variant="outline">{"+" + str(len(p.subfields) - 4)}</Badge>}
                    </div> if len(p.subfields) > 0 else <span className="text-xs text-muted-foreground">{"Not yet collected"}</span>}
                </div>
                <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-xs">
                    <span className="flex items-center gap-1 tabular-nums text-muted-foreground">
                        <HugeiconsIcon icon={Money03Icon} strokeWidth={2} className="size-3.5" />{grants_label}
                    </span>
                    {p.has_hiring and <span className="flex items-center gap-1 font-medium text-primary">
                        <HugeiconsIcon icon={SparklesIcon} strokeWidth={2} className="size-3.5" />{"Hiring statement found"}
                    </span>}
                </div>
            </CardContent>
        </Card>
    </Link>;
}

def:pub SearchPage() -> JsxElement {
    has results: list[ProfessorSummary] = [],
        options: FilterOptions | None = None,
        query: str = "",
        university: str = "",
        department: str = "",
        subfield: str = "",
        has_grant: bool = False,
        has_hiring: bool = False,
        loading: bool = True,
        error: str = "";

    async def run_search(q: str, u: str, d: str, s: str, g: bool, h: bool) -> None {
        loading = True;
        error = "";
        # The API server can still be starting right after a preview restart; retry briefly.
        found: list[ProfessorSummary] = [];
        for attempt in range(4) {
            try {
                found = await search_professors(q, u, d, s, g, h);
                results = found;
                loading = False;
                if jacIsLoggedIn() {
                    # Saved to the signed-in user's own history (server collapses keystroke-by-keystroke typing).
                    try { await record_search(q, u, d, s, g, h, len(found)); } except Exception {}
                }
                return;
            } except Exception {
                await new(Promise, lambda (resolve: any) { setTimeout(resolve, 1500 * (attempt + 1)); });
            }
        }
        error = "Could not reach the server. It may still be starting, try again in a moment.";
        loading = False;
    }

    loc = useLocation();

    async can with entry {
        # Prefill from the URL, e.g. when re-running a search from account history.
        p: any = new(URLSearchParams, loc.search);
        q0: str = p.get("q") or "";
        u0: str = p.get("university") or "";
        d0: str = p.get("department") or "";
        s0: str = p.get("subfield") or "";
        g0: bool = p.get("grant") == "1";
        h0: bool = p.get("hiring") == "1";
        query = q0; university = u0; department = d0; subfield = s0; has_grant = g0; has_hiring = h0;
        await run_search(q0, u0, d0, s0, g0, h0);
        try {
            options = await get_filter_options();
        } except Exception {
            options = None;
        }
    }

    def reset() -> None {
        query = ""; university = ""; department = ""; subfield = "";
        has_grant = False; has_hiring = False;
        run_search("", "", "", "", False, False);
    }

    unis: list[str] = options.universities if options else [];
    depts: list[str] = options.departments if options else [];
    subs: list[str] = options.subfields if options else [];
    count_label = "1 professor" if len(results) == 1 else str(len(results)) + " professors";

    return <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
        <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-semibold tracking-tight">{"Find a professor"}</h1>
            <p className="text-sm text-muted-foreground">{"Verified university faculty with their recent OpenAlex papers, research subfields, grants and hiring statements."}</p>
        </div>
        <Card className="shadow-sm">
            <CardContent className="flex flex-col gap-4">
                <div className="relative">
                    <HugeiconsIcon icon={Search01Icon} strokeWidth={2} className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        value={query}
                        placeholder="Search by professor name, e.g. Jessy Grizzle"
                        className="h-10 pl-9"
                        onChange={lambda (e: ChangeEvent) {
                            query = e.target.value;
                            run_search(e.target.value, university, department, subfield, has_grant, has_hiring);
                        }}
                    />
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="f-uni">{"University"}</Label>
                        <NativeSelect id="f-uni" className="w-full" value={university} onChange={lambda (e: ChangeEvent) {
                            university = e.target.value;
                            run_search(query, e.target.value, department, subfield, has_grant, has_hiring);
                        }}>
                            <NativeSelectOption value="">{"All universities"}</NativeSelectOption>
                            {for u in unis { <NativeSelectOption key={u} value={u}>{u}</NativeSelectOption> }}
                        </NativeSelect>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="f-dept">{"Department"}</Label>
                        <NativeSelect id="f-dept" className="w-full" value={department} onChange={lambda (e: ChangeEvent) {
                            department = e.target.value;
                            run_search(query, university, e.target.value, subfield, has_grant, has_hiring);
                        }}>
                            <NativeSelectOption value="">{"All departments"}</NativeSelectOption>
                            {for d in depts { <NativeSelectOption key={d} value={d}>{d}</NativeSelectOption> }}
                        </NativeSelect>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <Label htmlFor="f-sub">{"Recent paper subfield"}</Label>
                        <NativeSelect id="f-sub" className="w-full" value={subfield} onChange={lambda (e: ChangeEvent) {
                            subfield = e.target.value;
                            run_search(query, university, department, e.target.value, has_grant, has_hiring);
                        }}>
                            <NativeSelectOption value="">{"All subfields"}</NativeSelectOption>
                            {for s in subs { <NativeSelectOption key={s} value={s}>{s}</NativeSelectOption> }}
                        </NativeSelect>
                    </div>
                </div>
                <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                    <div className="flex items-center gap-2">
                        <Checkbox id="f-grant" checked={has_grant} onCheckedChange={lambda (v: any) {
                            has_grant = v == True;
                            run_search(query, university, department, subfield, v == True, has_hiring);
                        }} />
                        <Label htmlFor="f-grant">{"Has grant"}</Label>
                    </div>
                    <div className="flex items-center gap-2">
                        <Checkbox id="f-hire" checked={has_hiring} onCheckedChange={lambda (v: any) {
                            has_hiring = v == True;
                            run_search(query, university, department, subfield, has_grant, v == True);
                        }} />
                        <Label htmlFor="f-hire">{"Has hiring quote"}</Label>
                    </div>
                    <span className="ml-auto text-sm tabular-nums text-muted-foreground">{count_label}</span>
                </div>
            </CardContent>
        </Card>

        {error and <div className="flex items-center gap-3 text-sm text-destructive">
            <span>{error}</span>
            <Button variant="outline" size="sm" onClick={lambda { run_search(query, university, department, subfield, has_grant, has_hiring); }}>{"Retry"}</Button>
        </div>}

        {loading and len(results) == 0 and <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <Skeleton className="h-52 rounded-lg" />
            <Skeleton className="h-52 rounded-lg" />
            <Skeleton className="h-52 rounded-lg" />
        </div>}

        {not loading and not error and len(results) == 0 and <Empty className="border border-dashed bg-card pt-12 pb-12">
            <EmptyHeader>
                <EmptyMedia variant="icon"><HugeiconsIcon icon={UserGroupIcon} strokeWidth={2} /></EmptyMedia>
                <EmptyTitle>{"No professors match"}</EmptyTitle>
                <EmptyDescription>{"Clear the filters, or start the pipeline to import faculty from Michigan universities first."}</EmptyDescription>
            </EmptyHeader>
            <EmptyContent>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={reset}>{"Clear filters"}</Button>
                    <Link to="/pipeline"><Button>{"Open pipeline"}</Button></Link>
                </div>
            </EmptyContent>
        </Empty>}

        {len(results) > 0 and <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {for p in results[:60] { <ResultCard key={p.id} p={p} /> }}
        </div>}
        {len(results) > 60 and <p className="text-center text-sm text-muted-foreground">{"Showing the first 60 of " + str(len(results)) + " professors. Narrow by name, university, department or subfield."}</p>}
    </div>;
}
