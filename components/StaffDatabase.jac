import from "@jac/runtime" { Link }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" {
    Home01Icon,
    UserGroupIcon,
    CheckmarkCircle02Icon,
    SparklesIcon,
    Search01Icon
}
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from .ui.badge { Badge }
import from .ui.input { Input }
import from .ui.skeleton { Skeleton }
import from .ui.native_select { NativeSelect, NativeSelectOption }
import from .ui.table { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
import from .StaffNav { StaffNav }
import from ..services.staffdb {
    staff_overview,
    staff_institutions,
    staff_professors,
    DbOverview,
    DbInstitution,
    DbProfessor,
    DbProfessorPage
}
import from ..services.access { list_staff, promote_staff, revoke_staff, AccessResult }
import from .AuthContext { useAuth }

glob PAGE_SIZE: int = 25;

def:pub DbStat(label: str, value: int, icon: any) -> JsxElement {
    return
        <Card className="shadow-sm">
            <CardContent className="flex flex-col gap-3">
                <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground">{label}</span>
                    <div
                        className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary"
                    >
                        <HugeiconsIcon icon={icon} strokeWidth={2} className="size-4"/>
                    </div>
                </div>
                <span className="text-3xl font-semibold tracking-tight tabular-nums">
                    {str(value)}
                </span>
            </CardContent>
        </Card>;
}

def status_variant(s: str) -> str {
    if s == "MATCHED" {
        return "default";
    }
    if s == "UNRESOLVED" {
        return "destructive";
    }
    return "outline";
}

def:pub StaffDatabase() -> JsxElement {
    has overview: DbOverview | None = None,
        insts: list[DbInstitution] = [],
        page: DbProfessorPage | None = None,
        staff: list[str] = [],
        query: str = "",
        status: str = "",
        offset: int = 0,
        loading: bool = True,
        prof_loading: bool = False,
        error: str = "",
        new_staff: str = "",
        staff_msg: str = "";

    async def load_profs(q: str, st: str, off: int) -> None {
        prof_loading = True;
        try {
            page = await staff_professors(q, st, off, PAGE_SIZE);
        } except Exception as e {
            error = "Could not load professors: " + str(e);
        }
        prof_loading = False;
    }

    async def load_all() -> None {
        loading = True;
        error = "";
        try {
            overview = await staff_overview();
            insts = await staff_institutions();
            staff = await list_staff();
        } except Exception as e {
            error = "Could not load the database: " + str(e);
        }
        loading = False;
        await load_profs(query, status, 0);
    }

    async def add_staff() -> None {
        if not new_staff.strip() {
            return;
        }
        try {
            res: AccessResult = await promote_staff(new_staff.strip());
            staff_msg = res.message;
            if res.ok {
                new_staff = "";
                staff = await list_staff();
            }
        } except Exception as e {
            staff_msg = str(e);
        }
    }

    async def remove_staff(e: str) -> None {
        try {
            res: AccessResult = await revoke_staff(e);
            staff_msg = res.message;
            staff = await list_staff();
        } except Exception as ex {
            staff_msg = str(ex);
        }
    }

    async can with entry {
        await load_all();
    }

    auth = useAuth();
    who: any = auth["me"];
    is_owner: bool = bool(who and who.is_owner);

    total = page.total if page else 0;
    rows: list[DbProfessor] = page.rows if page else [];
    o = overview;

    return
        <div
            className="mx-auto flex w-full max-w-6xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6 animate-in fade-in slide-in-from-bottom-2 duration-500"
        >
            <StaffNav/>
            <div className="flex flex-col gap-1">
                <h1 className="text-2xl font-semibold tracking-tight">{"Database"}</h1>
                <p className="text-sm text-muted-foreground">
                    {"Everything the pipeline has stored: universities, professors, match status, papers, grants and hiring statements."}
                </p>
            </div>
            {error
                and <div className="flex items-center gap-3 text-sm text-destructive">
                    <span>{error}</span>
                    <Button variant="outline" size="sm" onClick={load_all}>
                        {"Retry"}
                    </Button>
                </div>}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <DbStat
                    label="Universities"
                    value={o.institutions if o else 0}
                    icon={Home01Icon}
                />
                <DbStat
                    label="Professors"
                    value={o.professors if o else 0}
                    icon={UserGroupIcon}
                />
                <DbStat
                    label="Matched to OpenAlex"
                    value={o.matched if o else 0}
                    icon={CheckmarkCircle02Icon}
                />
                <DbStat
                    label="Hiring statements"
                    value={o.hiring if o else 0}
                    icon={SparklesIcon}
                />
            </div>
            {o
                and <p className="-mt-2 text-xs text-muted-foreground tabular-nums">
                    {str(o.unresolved)
                        + " unresolved, "
                        + str(o.pending)
                        + " pending, "
                        + str(o.papers)
                        + " paper links, "
                        + str(o.grants)
                        + " grant links"}
                </p>}
            <Card className="shadow-sm">
                <CardHeader>
                    <CardTitle>{"Professors"}</CardTitle>
                    <CardDescription>
                        {"Search by name, department or university."}
                    </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                    <div className="flex flex-wrap items-center gap-3">
                        <div className="relative w-full max-w-sm">
                            <HugeiconsIcon
                                icon={Search01Icon}
                                strokeWidth={2}
                                className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
                            />
                            <Input
                                value={query}
                                placeholder="Name, department or university"
                                className="pl-8"
                                onChange={lambda (e: ChangeEvent) {
                                    query = e.target.value;
                                    offset = 0;
                                    load_profs(e.target.value, status, 0);
                                }}
                            />
                        </div>
                        <NativeSelect
                            value={status}
                            onChange={lambda (e: ChangeEvent) {
                                status = e.target.value;
                                offset = 0;
                                load_profs(query, e.target.value, 0);
                            }}
                        >
                            <NativeSelectOption value="">
                                {"All match statuses"}
                            </NativeSelectOption>
                            <NativeSelectOption value="MATCHED">
                                {"Matched"}
                            </NativeSelectOption>
                            <NativeSelectOption value="UNRESOLVED">
                                {"Unresolved"}
                            </NativeSelectOption>
                            <NativeSelectOption value="PENDING">
                                {"Pending"}
                            </NativeSelectOption>
                        </NativeSelect>
                        <span
                            className="ml-auto text-sm text-muted-foreground tabular-nums"
                        >
                            {str(total)
                                + (" professor" if total == 1 else " professors")}
                        </span>
                    </div>
                    {prof_loading
                        and len(rows) == 0
                        and <div className="flex flex-col gap-2">
                            <Skeleton className="h-10"/>
                            <Skeleton className="h-10"/>
                            <Skeleton className="h-10"/>
                        </div>}
                    {len(rows) > 0
                        and <div className="overflow-x-auto rounded-lg border">
                            <Table>
                                <TableHeader className="bg-muted/50">
                                    <TableRow>
                                        <TableHead>{"Professor"}</TableHead>
                                        <TableHead>{"University"}</TableHead>
                                        <TableHead>{"Status"}</TableHead>
                                        <TableHead>{"OpenAlex ID"}</TableHead>
                                        <TableHead className="text-right">
                                            {"Subfields"}
                                        </TableHead>
                                        <TableHead className="text-right">
                                            {"Grants"}
                                        </TableHead>
                                        <TableHead className="text-right">
                                            {"Hiring"}
                                        </TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {for r in rows {
                                        <TableRow
                                            key={r.id}
                                            className="hover:bg-muted/40"
                                        >
                                            <TableCell>
                                                <Link
                                                    to={"/professors/" + r.id}
                                                    className="flex flex-col hover:text-primary"
                                                >
                                                    <span className="font-medium">
                                                        {r.name}
                                                    </span>
                                                    <span
                                                        className="text-xs text-muted-foreground"
                                                    >
                                                        {r.title + " - " + r.department}
                                                    </span>
                                                </Link>
                                            </TableCell>
                                            <TableCell className="text-sm">
                                                {r.university}
                                            </TableCell>
                                            <TableCell>
                                                <Badge
                                                    variant={status_variant(
                                                        r.match_status
                                                    )}
                                                >
                                                    {r.match_status}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="font-mono text-xs">
                                                {r.openalex_author_id or "-"}
                                            </TableCell>
                                            <TableCell
                                                className="text-right tabular-nums"
                                            >
                                                {str(r.subfields)}
                                            </TableCell>
                                            <TableCell
                                                className="text-right tabular-nums"
                                            >
                                                {str(r.grants)}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                {"Yes" if r.has_hiring else "-"}
                                            </TableCell>
                                        </TableRow>
                                    }}
                                </TableBody>
                            </Table>
                        </div>}
                    {not prof_loading
                        and len(rows) == 0
                        and <p
                            className="pt-6 pb-6 text-center text-sm text-muted-foreground"
                        >
                            {"No professors match these filters."}
                        </p>}
                    {total > PAGE_SIZE
                        and <div className="flex items-center justify-end gap-2">
                            <span
                                className="text-xs text-muted-foreground tabular-nums"
                            >
                                {str(offset + 1)
                                    + "-"
                                    + str(min(offset + PAGE_SIZE, total))
                                    + " of "
                                    + str(total)}
                            </span>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={offset == 0}
                                onClick={lambda {
                                    offset = max(offset - PAGE_SIZE, 0);
                                    load_profs(
                                        query, status, max(offset - PAGE_SIZE, 0)
                                    );
                                }}
                            >
                                {"Previous"}
                            </Button>
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={offset + PAGE_SIZE >= total}
                                onClick={lambda {
                                    offset = offset + PAGE_SIZE;
                                    load_profs(query, status, offset + PAGE_SIZE);
                                }}
                            >
                                {"Next"}
                            </Button>
                        </div>}
                </CardContent>
            </Card>
            <div className="grid gap-6 lg:grid-cols-3">
                <Card className="shadow-sm lg:col-span-2">
                    <CardHeader>
                        <CardTitle>{"Universities"}</CardTitle>
                        <CardDescription>
                            {str(len(insts)) + " in the database"}
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        {loading and <Skeleton className="h-40"/>}{not loading
                            and <div
                                className="max-h-96 overflow-auto rounded-lg border"
                            >
                                <Table>
                                    <TableHeader className="bg-muted/50">
                                        <TableRow>
                                            <TableHead>{"University"}</TableHead>
                                            <TableHead>{"State"}</TableHead>
                                            <TableHead className="text-right">
                                                {"Professors"}
                                            </TableHead>
                                            <TableHead className="text-right">
                                                {"Matched"}
                                            </TableHead>
                                            <TableHead className="text-right">
                                                {"Papers"}
                                            </TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {for i in insts {
                                            <TableRow key={i.id}>
                                                <TableCell>
                                                    <div className="flex flex-col">
                                                        <span className="font-medium">
                                                            {i.name}
                                                        </span>
                                                        <span
                                                            className="text-xs text-muted-foreground"
                                                        >
                                                            {i.tier
                                                                + (
                                                                    " - " + i.state
                                                                        if i.state
                                                                        else ""
                                                                )}
                                                        </span>
                                                    </div>
                                                </TableCell>
                                                <TableCell>
                                                    <Badge
                                                        variant={"default"
                                                            if i.pipeline_state
                                                                == "DONE"
                                                            else "secondary"}
                                                    >
                                                        {i.pipeline_state}
                                                    </Badge>
                                                </TableCell>
                                                <TableCell
                                                    className="text-right tabular-nums"
                                                >
                                                    {str(i.professors)}
                                                </TableCell>
                                                <TableCell
                                                    className="text-right tabular-nums"
                                                >
                                                    {str(i.matched)}
                                                </TableCell>
                                                <TableCell
                                                    className="text-right tabular-nums"
                                                >
                                                    {str(i.papers)}
                                                </TableCell>
                                            </TableRow>
                                        }}
                                    </TableBody>
                                </Table>
                            </div>}
                    </CardContent>
                </Card>
                <Card className="shadow-sm">
                    <CardHeader>
                        <CardTitle>{"Staff accounts"}</CardTitle>
                        <CardDescription>
                            {"Staff can run the pipeline and see this database. Only the site owner can add or remove staff."}
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-3">
                        <div className="flex flex-col gap-1">
                            {for e in staff {
                                <div
                                    key={e}
                                    className="flex items-center justify-between gap-2 rounded-md bg-muted/60 pt-1.5 pb-1.5 pl-2.5 pr-1.5"
                                >
                                    <span className="truncate text-sm">{e}</span>
                                    {is_owner
                                        and not e.endswith("(owner)")
                                        and <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={lambda { remove_staff(e); }}
                                        >
                                            {"Remove"}
                                        </Button>}
                                </div>
                            }}
                            {len(staff) == 0
                                and not loading
                                and <span className="text-sm text-muted-foreground">
                                    {"No staff emails recorded."}
                                </span>}
                        </div>
                        {is_owner
                            and <div className="flex gap-2">
                                <Input
                                    value={new_staff}
                                    type="email"
                                    placeholder="colleague@university.edu"
                                    className="h-9"
                                    onChange={lambda (e: ChangeEvent) { new_staff = e.target.value; }}
                                />
                                <Button
                                    variant="outline"
                                    onClick={add_staff}
                                    disabled={not new_staff.strip()}
                                >
                                    {"Add staff"}
                                </Button>
                            </div>}
                        {staff_msg
                            and <p className="text-xs text-muted-foreground">
                                {staff_msg}
                            </p>}
                        {is_owner
                            and <p className="text-xs text-muted-foreground">
                                {"They get staff access the next time they sign in with that email."}
                            </p>}
                    </CardContent>
                </Card>
            </div>
        </div>;
}
