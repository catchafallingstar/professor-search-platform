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
sv import from ..services.queue { list_staff_review, resolve_staff_review, requeue_staff_review, ReviewItem, StepResult }

glob TASK_LABELS: dict[str, str] = {
    "OPENALEX_IDENTITY": "OpenAlex identity",
    "GRANT_SEARCH": "Grant search",
    "HIRING_EXTRACTION": "Hiring check",
    "FACULTY_DIRECTORY_DISCOVERY": "Faculty directory",
    "FACULTY_EXTRACTION": "Faculty list",
    "PROFILE_EXTRACTION": "Faculty profile",
    "HIRING": "Hiring check"
};

def:pub StaffReview() -> JsxElement {
    has items: list[ReviewItem] = [],
        loading: bool = True,
        requeuing: bool = False,
        notice: str = "",
        error: str = "";

    async def requeue() -> None {
        requeuing = True;
        try {
            r: StepResult = await requeue_staff_review();
            notice = r.message;
            error = "";
        } except Exception as e {
            error = "Could not re-queue: " + str(e);
        }
        requeuing = False;
    }

    async def load() -> None {
        try {
            items = await list_staff_review(200);
            error = "";
        } except Exception as e {
            error = "Could not load the review list: " + str(e);
        }
        loading = False;
    }

    async def resolve(item_id: str) -> None {
        try {
            _r: StepResult = await resolve_staff_review(item_id);
            items = [i for i in items if i.id != item_id];
        } except Exception as e {
            error = "Could not update the item: " + str(e);
        }
    }

    async can with entry {
        await load();
    }

    return <Card className="shadow-sm">
        <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex flex-col gap-1">
                    <CardTitle>{"Needs staff review"}</CardTitle>
                    <CardDescription>{"Tasks the pipeline could not finish on its own, such as professors with no confirmed OpenAlex identity after the Google Scholar check."}</CardDescription>
                </div>
                <div className="flex items-center gap-2">
                    {not loading and <Badge variant="secondary">{str(len(items)) + " open"}</Badge>}
                    <Button variant="outline" size="sm" onClick={lambda { loading = True; load(); }}>{"Refresh"}</Button>
                    <Button size="sm" onClick={requeue} disabled={requeuing or len(items) == 0}>
                        {requeuing and <Spinner />}{"Re-queue"}
                    </Button>
                </div>
            </div>
        </CardHeader>
        <CardContent>
            {notice and <p className="pb-3 text-sm text-muted-foreground">{notice}</p>}
            {error and <p className="pb-3 text-sm text-destructive">{error}</p>}
            {loading and <div className="flex items-center gap-2 pt-6 pb-6 text-sm text-muted-foreground"><Spinner />{"Loading review items..."}</div>}
            {not loading and len(items) == 0 and not error and <Empty className="border border-dashed pt-10 pb-10">
                <EmptyHeader>
                    <EmptyMedia variant="icon"><HugeiconsIcon icon={CheckmarkCircle02Icon} strokeWidth={2} /></EmptyMedia>
                    <EmptyTitle>{"Nothing needs review"}</EmptyTitle>
                    <EmptyDescription>{"Items appear here when an identity cannot be matched or every AI model fails."}</EmptyDescription>
                </EmptyHeader>
            </Empty>}
            {not loading and len(items) > 0 and <div className="overflow-x-auto">
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>{"Task"}</TableHead>
                            <TableHead>{"Professor / university"}</TableHead>
                            <TableHead>{"Problem"}</TableHead>
                            <TableHead>{"Checked"}</TableHead>
                            <TableHead className="text-right">{"Action"}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {for it in items {
                            <TableRow key={it.id}>
                                <TableCell><Badge variant="outline">{TASK_LABELS.get(it.task_type, it.task_type)}</Badge></TableCell>
                                <TableCell>
                                    <div className="flex flex-col">
                                        {it.professor_id and <Link to={"/professors/" + it.professor_id} className="font-medium hover:underline">{it.professor}</Link>}
                                        {not it.professor_id and <span className="font-medium">{it.university}</span>}
                                        <span className="text-xs text-muted-foreground">{(it.department + ", " if it.department else "") + (it.university if it.professor_id else "")}</span>
                                    </div>
                                </TableCell>
                                <TableCell>
                                    <div className="flex max-w-md flex-col gap-1">
                                        <span className="text-sm">{it.detail or it.reason}</span>
                                        {it.source_url and <a href={it.source_url} target="_blank" rel="noreferrer" className="truncate text-xs text-primary hover:underline">{it.source_url}</a>}
                                    </div>
                                </TableCell>
                                <TableCell><span className="text-xs tabular-nums text-muted-foreground">{it.attempted_at[:10]}</span></TableCell>
                                <TableCell className="text-right">
                                    <Button variant="ghost" size="sm" onClick={lambda { resolve(it.id); }}>{"Mark handled"}</Button>
                                </TableCell>
                            </TableRow>
                        }}
                    </TableBody>
                </Table>
            </div>}
        </CardContent>
    </Card>;
}
