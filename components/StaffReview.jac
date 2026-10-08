"""Needs staff review: tasks the pipeline could not finish on its own (unresolved OpenAlex
identities after the Google Scholar check, AI fallbacks that failed). Staff mark items handled."""

import from "@jac/runtime" { Link }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { CheckmarkCircle02Icon }
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from .ui.badge { Badge }
import from .ui.spinner { Spinner }
import from .ui.table { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
import from .ui.empty { Empty, EmptyHeader, EmptyMedia, EmptyTitle, EmptyDescription }
import from react { useEffect }
import from ..services.queue {
    list_staff_review,
    resolve_staff_review,
    requeue_staff_review,
    list_recent_jobs,
    ReviewItem,
    StepResult,
    QueueJob
}

glob RESULT_LABELS: dict[str, str] = {
         "FIXED": "Fixed",
         "FIXED_MATCHED": "Fixed: OpenAlex identity matched",
         "FIXED_SCHOLAR": "Fixed: Google Scholar profile",
         "FIXED_FACULTY_PAGE": "Fixed: publications from faculty page",
         "FIXED_DIRECTORY": "Fixed: faculty directory rediscovered",
         "FIXED_FACULTY_LIST": "Fixed: faculty list extracted",
         "FIXED_COVERAGE": "Fixed: university faculty coverage complete",
         "NO_RESULT_FOUND": "Retried: still no result",
         "RETRY_LATER": "Waiting: a step will run again shortly",
         "PAGE_UNREACHABLE": "Retried: page still blocked",
         "STILL_FAILING": "Retried: still failing"
     };

def result_label(r: str) -> str {
    if r.startswith("FIXED_GRANTS_") {
        return "Fixed: " + r.replace("FIXED_GRANTS_", "") + " grants found";
    }
    return RESULT_LABELS.get(r, r.replace("_", " ").lower());
}

def:pub QueueBadge(status: str) -> JsxElement {
    if status == "RUNNING" {
        return
            <Badge className="gap-1">
                <Spinner className="size-3"/>
                {"Running"}
            </Badge>;
    }
    if status == "QUEUED" or status == "WAITING" {
        return <Badge variant="secondary">{"Waiting"}</Badge>;
    }
    if status == "DONE" {
        return <Badge variant="outline">{"Done"}</Badge>;
    }
    if status == "FAILED" {
        return <Badge variant="destructive">{"Failed"}</Badge>;
    }
    if status == "RETRIED" {
        return <Badge variant="outline">{"Retried"}</Badge>;
    }
    return <span></span>;
}

glob TASK_LABELS: dict[str, str] = {
         "OPENALEX_IDENTITY": "OpenAlex identity",
         "GRANT_SEARCH": "Grant search",
         "HIRING_EXTRACTION": "Hiring check",
         "FACULTY_DIRECTORY_DISCOVERY": "Faculty directory",
         "FACULTY_EXTRACTION": "Faculty list",
         "UNIVERSITY_COVERAGE": "University faculty coverage",
         "PROFILE_EXTRACTION": "Faculty profile",
         "HIRING": "Hiring check"
     };

def:pub StaffReview() -> JsxElement {
    has items: list[ReviewItem] = [],
        jobs: list[QueueJob] = [],
        loading: bool = True,
        requeuing: bool = False,
        notice: str = "",
        error: str = "";

    async def load() -> None {
        try {
            items = await list_staff_review(200);
            jobs = await list_recent_jobs(10);
            error = "";
        } except Exception as e {
            error = "Could not load the review list: " + str(e);
        }
        loading = False;
    }

    async def requeue() -> None {
        requeuing = True;
        try {
            r: StepResult = await requeue_staff_review();
            notice = r.message;
            error = "";
            await load();
        } except Exception as e {
            error = "Could not re-queue: " + str(e);
        }
        requeuing = False;
    }

    busy: bool = len(
        [
            j
            for j in jobs
            if j.status == "QUEUED" or j.status == "RUNNING"
        ]
    )
        > 0;

    # while jobs are waiting or running, refresh every 10 s so results appear on their own
    # React requires the cleanup to be a function (never None), so the timer always exists and
    # only reloads while jobs are waiting or running
    useEffect(
        lambda {
            timer = setInterval(lambda { if busy { load(); }}, 10000);
            return lambda { clearInterval(timer); };
        },
        [busy]
    );

    async def resolve(item_id: str) -> None {
        try {
            _r: StepResult = await resolve_staff_review(item_id);
            items = [
                i
                for i in items
                if i.id != item_id
            ];
        } except Exception as e {
            error = "Could not update the item: " + str(e);
        }
    }

    async can with entry {
        await load();
    }

    return
        <Card className="shadow-sm">
            <CardHeader>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex flex-col gap-1">
                        <CardTitle>{"Needs staff review"}</CardTitle>
                        <CardDescription>
                            {"Tasks the pipeline could not finish on its own, such as professors with no confirmed OpenAlex identity after the Google Scholar check."}
                        </CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                        {not loading
                            and <Badge variant="secondary">
                                {str(len(items)) + " open"}
                            </Badge>}
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={lambda {
                                loading = True;
                                load();
                            }}
                        >
                            {"Refresh"}
                        </Button>
                        <Button
                            size="sm"
                            onClick={requeue}
                            disabled={requeuing or len(items) == 0}
                        >
                            {requeuing and <Spinner/>}{"Re-queue"}
                        </Button>
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                {notice
                    and <p className="pb-3 text-sm text-muted-foreground">{notice}</p>}
                {error and <p className="pb-3 text-sm text-destructive">{error}</p>}
                {loading
                    and <div
                        className="flex items-center gap-2 pt-6 pb-6 text-sm text-muted-foreground"
                    >
                        <Spinner/>
                        {"Loading review items..."}
                    </div>}
                {not loading
                    and len(items) == 0
                    and not error
                    and <Empty className="border border-dashed pt-10 pb-10">
                        <EmptyHeader>
                            <EmptyMedia variant="icon">
                                <HugeiconsIcon
                                    icon={CheckmarkCircle02Icon}
                                    strokeWidth={2}
                                />
                            </EmptyMedia>
                            <EmptyTitle>{"Nothing needs review"}</EmptyTitle>
                            <EmptyDescription>
                                {"Items appear here when an identity cannot be matched or every AI model fails."}
                            </EmptyDescription>
                        </EmptyHeader>
                    </Empty>}
                {not loading
                    and len(items) > 0
                    and <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>{"Task"}</TableHead>
                                    <TableHead>{"Professor / university"}</TableHead>
                                    <TableHead>{"Problem"}</TableHead>
                                    <TableHead>{"Status"}</TableHead>
                                    <TableHead className="text-right">
                                        {"Action"}
                                    </TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {for it in items {
                                    <TableRow key={it.id}>
                                        <TableCell>
                                            <Badge variant="outline">
                                                {TASK_LABELS.get(
                                                    it.task_type, it.task_type
                                                )}
                                            </Badge>
                                        </TableCell>
                                        <TableCell>
                                            <div className="flex flex-col">
                                                {it.professor_id
                                                    and <Link
                                                        to={"/professors/"
                                                            + it.professor_id}
                                                        className="font-medium hover:underline"
                                                    >
                                                        {it.professor}
                                                    </Link>}
                                                {not it.professor_id
                                                    and <span className="font-medium">
                                                        {it.university}
                                                    </span>}
                                                <span
                                                    className="text-xs text-muted-foreground"
                                                >
                                                    {(
                                                        it.department + ", "
                                                            if it.department
                                                            else ""
                                                    )
                                                        + (
                                                            it.university
                                                                if it.professor_id
                                                                else ""
                                                        )}
                                                </span>
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div
                                                className="flex max-w-md flex-col gap-1"
                                            >
                                                <span className="text-sm">
                                                    {it.detail or it.reason}
                                                </span>
                                                {it.source_url
                                                    and <a
                                                        href={it.source_url}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="truncate text-xs text-primary hover:underline"
                                                    >
                                                        {it.source_url}
                                                    </a>}
                                            </div>
                                        </TableCell>
                                        <TableCell>
                                            <div
                                                className="flex flex-col items-start gap-1"
                                            >
                                                <QueueBadge status={it.queue_status}/>
                                                {it.last_retry_result
                                                    and <span
                                                        className="max-w-48 text-xs text-muted-foreground"
                                                    >
                                                        {result_label(
                                                            it.last_retry_result
                                                        )}
                                                    </span>}
                                                <span
                                                    className="text-xs tabular-nums text-muted-foreground"
                                                >
                                                    {(
                                                        it.last_retry_at
                                                        or it.attempted_at
                                                    )[:16].replace(
                                                        "T", " "
                                                    )}
                                                </span>
                                            </div>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                onClick={lambda { resolve(it.id); }}
                                            >
                                                {"Mark handled"}
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                }}
                            </TableBody>
                        </Table>
                    </div>}
                <div className="mt-6 flex flex-col gap-2 border-t pt-4">
                    <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium">{"Recent work"}</span>
                        <span className="text-xs text-muted-foreground">
                            {"Running and waiting first (re-queued items at the top), then the last finished jobs"}
                        </span>
                    </div>
                    {not loading
                        and len(jobs) == 0
                        and <p className="text-sm text-muted-foreground">
                            {"No queued or recent jobs."}
                        </p>}
                    {len(jobs) > 0
                        and <div className="flex flex-col">
                            {for j in jobs {
                                <div
                                    key={j.id}
                                    className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b pt-2 pb-2 text-sm last:border-b-0"
                                >
                                    <QueueBadge status={j.status}/>
                                    <span className="font-medium">
                                        {j.professor or "Unknown professor"}
                                    </span>
                                    <span className="text-xs text-muted-foreground">
                                        {TASK_LABELS.get(
                                            j.task, j.task.replace("_", " ").lower()
                                        )}
                                    </span>
                                    {j.result
                                        and <span
                                            className="text-xs text-muted-foreground"
                                        >
                                            {result_label(j.result)}
                                        </span>}
                                    <span
                                        className="ml-auto text-xs tabular-nums text-muted-foreground"
                                    >
                                        {j.when[:16].replace("T", " ")}
                                    </span>
                                </div>
                            }}
                        </div>}
                </div>
            </CardContent>
        </Card>;
}