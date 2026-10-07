import from react { useEffect }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" {
    Home01Icon, UserGroupIcon, CheckmarkCircle02Icon, SparklesIcon, Alert01Icon
}
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from .ui.badge { Badge }
import from .ui.spinner { Spinner }
import from .ui.table { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
import from .ui.empty { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription, EmptyContent }
sv import from ..services.queue {
    load_priority_universities, get_pipeline_status, run_step_as_staff, get_worker_state,
    set_worker, reset_institution, get_config_status, add_openalex_key, check_llm, run_maintenance_now,
    get_database_upgrade_state, run_database_upgrade,
    InstitutionStatus, StepResult, WorkerState, ConfigStatus, DatabaseUpgradeState
}
import from .OpenAlexKeys { OpenAlexKeys }
import from .StaffNav { StaffNav }
import from .DataBackup { DataBackup }
import from .StaffReview { StaffReview }

def:pub StatTile(label: str, value: int, icon: any) -> JsxElement {
    return <Card className="shadow-sm">
        <CardContent className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">{label}</span>
                <div className="flex size-9 items-center justify-center rounded-lg bg-primary/10 text-primary">
                    <HugeiconsIcon icon={icon} strokeWidth={2} className="size-4" />
                </div>
            </div>
            <span className="text-3xl font-semibold tracking-tight tabular-nums">{str(value)}</span>
        </CardContent>
    </Card>;
}

def:pub ConfigRow(label: str, ok: bool, detail: str) -> JsxElement {
    return <div className="flex items-start justify-between gap-4 border-b pt-2.5 pb-2.5 text-sm last:border-b-0">
        <div className="flex flex-col gap-0.5">
            <span className="font-medium">{label}</span>
            <span className="text-xs text-muted-foreground">{detail}</span>
        </div>
        <Badge variant={"secondary" if ok else "outline"}>{"Ready" if ok else "Not set"}</Badge>
    </div>;
}

def:pub PipelineAdmin() -> JsxElement {
    has rows: list[InstitutionStatus] = [],
        worker: WorkerState | None = None,
        config: ConfigStatus | None = None,
        upgrade: DatabaseUpgradeState | None = None,
        loading: bool = True,
        busy: bool = False,
        upgrading: bool = False,
        llm_msg: str = "",
        upgrade_msg: str = "",
        error: str = "";

    async def refresh() -> None {
        try {
            rows = await get_pipeline_status();
            worker = await get_worker_state();
            upgrade = await get_database_upgrade_state();
            error = "";
        } except Exception as e {
            error = "Could not load pipeline status: " + str(e);
        }
        loading = False;
    }

    async def load_queue() -> None {
        busy = True;
        try { rows = await load_priority_universities(); } except Exception as e { error = str(e); }
        busy = False;
    }

    async def step() -> None {
        busy = True;
        try {
            _r: StepResult = await run_step_as_staff();
            await refresh();
        } except Exception as e { error = str(e); }
        busy = False;
    }

    async def toggle() -> None {
        busy = True;
        running = worker.running if worker else False;
        try {
            worker = await set_worker(not running);
        } except Exception as e { error = str(e); }
        busy = False;
    }

    async def test_llm() -> None {
        busy = True;
        try { r: StepResult = await check_llm(); llm_msg = r.message; } except Exception as e { llm_msg = str(e); }
        busy = False;
    }

    async def maintain() -> None {
        busy = True;
        try { r: StepResult = await run_maintenance_now(); llm_msg = r.message; await refresh(); } except Exception as e { llm_msg = str(e); }
        busy = False;
    }

    async def run_upgrade() -> None {
        upgrading = True;
        upgrade_msg = "";
        try {
            r: StepResult = await run_database_upgrade();
            upgrade_msg = r.message;
            upgrade = await get_database_upgrade_state();
            await refresh();
        } except Exception as e {
            upgrade_msg = "Database upgrade failed: " + str(e);
        }
        upgrading = False;
    }

    async def reset(iid: str) -> None {
        try { await reset_institution(iid); await refresh(); } except Exception as e { error = str(e); }
    }

    async can with entry {
        try { config = await get_config_status(); } except Exception { config = None; }
        try { upgrade = await get_database_upgrade_state(); } except Exception { upgrade = None; }
        await refresh();
    }

    useEffect(lambda {
        timer = setInterval(lambda { refresh(); }, 5000);
        return lambda { clearInterval(timer); };
    }, []);

    total_profs = 0; total_done = 0; total_hiring = 0; finished = 0;
    for r in rows {
        total_profs += r.professors;
        total_done += r.processed;
        total_hiring += r.hiring;
        if r.pipeline_state == "DONE" { finished += 1; }
    }
    running = worker.running if worker else False;
    upgrade_running = bool(upgrade and upgrade.status == "RUNNING");
    llm_ok = config.llm_key if config else False;
    log: list[str] = worker.log if worker else [];

    return <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 pt-8 pb-12 pl-6 pr-6 animate-in fade-in slide-in-from-bottom-2 duration-500">
        <StaffNav />
        <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex flex-col gap-1">
                <h1 className="text-2xl font-semibold tracking-tight">{"Ingestion pipeline"}</h1>
                <p className="text-sm text-muted-foreground">{"All IPEDS research universities (R1 and R2): Michigan first, then surrounding states, then the rest. Everything is saved to MongoDB as it runs; a full refresh runs every 30 days."}</p>
            </div>
            <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={step} disabled={busy or running or len(rows) == 0}>{"Run one step"}</Button>
                <Button className="shadow-sm" onClick={toggle} disabled={busy}>
                    {busy and <Spinner />}{"Pause auto-processing" if running else "Start auto-processing"}
                </Button>
            </div>
        </div>

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatTile label="Universities finished" value={finished} icon={Home01Icon} />
            <StatTile label="Professors imported" value={total_profs} icon={UserGroupIcon} />
            <StatTile label="Professors processed" value={total_done} icon={CheckmarkCircle02Icon} />
            <StatTile label="Hiring statements" value={total_hiring} icon={SparklesIcon} />
        </div>

        {error and <div className="text-sm text-destructive">{error}</div>}
        {config and config.rate_limited and <div className="flex items-start gap-2 rounded-lg border bg-card pt-3 pb-3 pl-4 pr-4 text-sm shadow-sm">
            <HugeiconsIcon icon={Alert01Icon} strokeWidth={2} className="mt-0.5 size-4 shrink-0 text-destructive" />
            <span>{config.rate_limit_message + " Faculty imports from university sites continue meanwhile; matching resumes automatically."}</span>
        </div>}

        <Card className="shadow-sm">
            <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex flex-col gap-1">
                        <CardTitle>{"One-time database upgrade"}</CardTitle>
                        <CardDescription>{"Repairs legacy MongoDB records created by older crawler logic: credential-decorated names, shared directory URLs, stale identity evidence, duplicate authors and bad department labels."}</CardDescription>
                    </div>
                    {upgrade and upgrade.done and <Badge variant="secondary">{"Completed"}</Badge>}
                </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
                {upgrade and <p className="text-sm text-muted-foreground">{upgrade.message}</p>}
                {not upgrade or (not upgrade.done and upgrade.status != "RUNNING") and <p className="text-sm text-muted-foreground">{"Pause auto-processing first. Run this once, then restart auto-processing and re-queue the old Staff Review jobs."}</p>}
                <div className="flex flex-wrap items-center gap-2">
                    <Button
                        variant={upgrade and upgrade.done ? "outline" : "default"}
                        onClick={run_upgrade}
                        disabled={upgrading or running or bool(upgrade and upgrade.done)}
                    >
                        {upgrading and <Spinner />}
                        {("Database upgrade completed" if upgrade and upgrade.done else ("Running database upgrade..." if upgrading or (upgrade and upgrade.status == "RUNNING") else "Run database upgrade"))}
                    </Button>
                    {running and not (upgrade and upgrade.done) and <span className="text-xs text-destructive">{"Pause auto-processing before running the upgrade."}</span>}
                </div>
                {upgrade_msg and <p className="text-sm text-muted-foreground">{upgrade_msg}</p>}
            </CardContent>
        </Card>

        <div className="grid gap-6 lg:grid-cols-3">
            <Card className="shadow-sm lg:col-span-1">
                <CardHeader>
                    <CardTitle>{"Connections"}</CardTitle>
                    <CardDescription>{"Add keys in Settings > Environment, then restart the preview."}</CardDescription>
                </CardHeader>
                <CardContent className="flex flex-col">
                    <ConfigRow label="OpenAlex API" ok={config.openalex_ok if config else False} detail="Tested live. No key required." />
                    <ConfigRow label="OPENALEX_MAILTO (optional)" ok={config.openalex_mailto if config else False} detail="Your email, for OpenAlex's faster polite pool." />
                    <ConfigRow
                        label="OpenAlex keys"
                        ok={config.openalex_key if config else False}
                        detail={(str(config.keys_available) + " of " + str(config.keys_total + 1) + " credentials usable (keys + no-key pool). Manage them below.") if config else "Manage them below."}
                    />
                    <ConfigRow label="LLM (Ollama or a cloud key)" ok={llm_ok} detail="Reads faculty profiles, finds directory pages and hiring statements." />
                    {not llm_ok and <div className="mt-3 flex items-start gap-2 rounded-lg bg-muted pt-2.5 pb-2.5 pl-3 pr-3 text-xs">
                        <HugeiconsIcon icon={Alert01Icon} strokeWidth={2} className="mt-0.5 size-3.5 shrink-0" />
                        <span>{"Without an LLM, faculty, papers and subfields are still imported. Set LLM_MODEL and OLLAMA_API_BASE (or a cloud key) to add hiring checks."}</span>
                    </div>}
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={test_llm} disabled={busy}>{"Check LLM connection"}</Button>
                        <Button variant="outline" size="sm" onClick={maintain} disabled={busy}>{"Run maintenance now"}</Button>
                    </div>
                    {llm_msg and <p className="mt-2 text-xs text-muted-foreground">{llm_msg}</p>}
                </CardContent>
            </Card>
            <Card className="shadow-sm lg:col-span-2">
                <CardHeader>
                    <CardTitle>{"Worker activity"}</CardTitle>
                    <CardDescription>{("Running" if running else "Paused") + (" - " + str(worker.steps) + " steps" if worker else "")}</CardDescription>
                </CardHeader>
                <CardContent>
                    {<div className="flex max-h-56 flex-col gap-1 overflow-y-auto font-mono text-xs">
                        {for line in log { <span key={line} className="text-muted-foreground">{line}</span> }}
                    </div> if len(log) > 0 else <p className="text-sm text-muted-foreground">{"No activity yet."}</p>}
                </CardContent>
            </Card>
        </div>

        <div className="grid gap-6 lg:grid-cols-3">
            <div className="lg:col-span-2"><OpenAlexKeys /></div>
            <DataBackup />
        </div>

        <Card className="shadow-sm">
            <CardHeader>
                <CardTitle>{"University queue"}</CardTitle>
                <CardDescription>{"Processed in priority order. States: QUEUED, CRAWLING, PROCESSING, UNIVERSITY_AWARDS, DONE."}</CardDescription>
            </CardHeader>
            <CardContent>
                {loading and <div className="flex justify-center pt-10 pb-10"><Spinner /></div>}
                {not loading and len(rows) == 0 and <Empty className="border border-dashed pt-12 pb-12">
                    <EmptyHeader>
                        <EmptyMedia variant="icon"><HugeiconsIcon icon={Home01Icon} strokeWidth={2} /></EmptyMedia>
                        <EmptyTitle>{"Queue is empty"}</EmptyTitle>
                        <EmptyDescription>{"Load the priority list: Michigan, surrounding states and the top 30 national universities."}</EmptyDescription>
                    </EmptyHeader>
                    <EmptyContent><Button onClick={load_queue} disabled={busy}>{"Load priority universities"}</Button></EmptyContent>
                </Empty>}
                {not loading and len(rows) > 0 and <div className="overflow-x-auto rounded-lg border">
                    <Table>
                        <TableHeader className="bg-muted/50">
                            <TableRow>
                                <TableHead>{"University"}</TableHead>
                                <TableHead>{"Priority"}</TableHead>
                                <TableHead>{"State"}</TableHead>
                                <TableHead className="text-right">{"Coverage"}</TableHead>
                                <TableHead className="text-right">{"Professors"}</TableHead>
                                <TableHead className="text-right">{"Matched"}</TableHead>
                                <TableHead className="text-right">{"Papers"}</TableHead>
                                <TableHead className="text-right">{"Grants"}</TableHead>
                                <TableHead className="text-right">{"Hiring"}</TableHead>
                                <TableHead className="text-right">{""}</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {for r in rows {
                                <TableRow key={r.id} className="hover:bg-muted/40">
                                    <TableCell>
                                        <div className="flex flex-col">
                                            <span className="font-medium">{r.name}</span>
                                            <span className="text-xs text-muted-foreground">{r.city + ", " + r.state}</span>
                                        </div>
                                    </TableCell>
                                    <TableCell><span className="text-xs">{r.priority_reason}</span></TableCell>
                                    <TableCell>
                                        <div className="flex flex-col gap-1">
                                            <Badge variant={"default" if r.pipeline_state == "DONE" else ("outline" if r.pipeline_state == "QUEUED" else "secondary")}>{r.pipeline_state}</Badge>
                                            {r.pipeline_note and <span className="max-w-56 text-xs text-muted-foreground">{r.pipeline_note}</span>}
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-right">
                                        <div className="flex flex-col items-end gap-1">
                                            <span className="tabular-nums">{(str(r.coverage_readable) + " / " + str(r.coverage_units)) if r.coverage_units else "—"}</span>
                                            {r.coverage_state and <Badge variant={"secondary" if r.coverage_state == "COMPLETE" else "outline"}>{r.coverage_state}</Badge>}
                                        </div>
                                    </TableCell>
                                    <TableCell className="text-right tabular-nums">{str(r.processed) + " / " + str(r.professors)}</TableCell>
                                    <TableCell className="text-right tabular-nums">{str(r.matched)}</TableCell>
                                    <TableCell className="text-right tabular-nums">{str(r.papers)}</TableCell>
                                    <TableCell className="text-right tabular-nums">{str(r.grants)}</TableCell>
                                    <TableCell className="text-right tabular-nums">{str(r.hiring)}</TableCell>
                                    <TableCell className="text-right">
                                        {r.pipeline_state != "QUEUED" and <Button variant="ghost" size="sm" onClick={lambda { reset(r.id); }}>{"Requeue"}</Button>}
                                    </TableCell>
                                </TableRow>
                            }}
                        </TableBody>
                    </Table>
                </div>}
            </CardContent>
        </Card>
        <StaffReview />
    </div>;
}
