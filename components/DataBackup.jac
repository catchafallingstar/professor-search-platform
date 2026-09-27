import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Download01Icon, Upload01Icon }
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
sv import from ..services.queue { download_snapshot, upload_snapshot, snapshot_info, StepResult }

def:pub DataBackup() -> JsxElement {
    has info: str = "",
        busy: bool = False,
        message: str = "";

    async def refresh() -> None {
        try {
            r: StepResult = await snapshot_info();
            info = r.message;
        } except Exception as e { info = "Could not read backup status: " + str(e); }
    }

    async def save_backup() -> None {
        busy = True;
        message = "";
        body: str = "";
        try {
            body = await download_snapshot();
        } except Exception as e { message = str(e); }
        busy = False;
        if not body {
            message = message or "Nothing to download yet.";
            return;
        }
        blob: any = new(Blob, [body], {"type": "application/json"});  # jac:ignore[E1053]
        url: any = URL.createObjectURL(blob);  # jac:ignore[E1032]
        link: any = document.createElement("a");
        stamp: any = new(Date).toISOString();  # jac:ignore[E1053]
        link.setAttribute("href", url);
        link.setAttribute("download", "professor-atlas-" + str(stamp)[:10] + ".json");
        link.click();
        URL.revokeObjectURL(url);  # jac:ignore[E1032]
        message = "Downloaded. Keep this file; you can upload it to restore everything.";
        await refresh();
    }

    async def upload_file(e: any) -> None {
        f: any = e.target.files[0] if e.target.files else None;
        if not f { return; }
        busy = True;
        message = "Uploading and restoring...";
        text: str = "";
        try {
            text = await f.text();
            r: StepResult = await upload_snapshot(text);
            message = r.message;
        } except Exception as ex { message = "Restore failed: " + str(ex); }
        busy = False;
        e.target.value = "";
        await refresh();
    }

    async can with entry { await refresh(); }

    return <Card className="shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
                <CardTitle>{"Backup"}</CardTitle>
                <CardDescription>{"Everything is saved automatically as the pipeline runs. Download a copy to keep it safe outside the preview; upload it to restore into an empty database."}</CardDescription>
            </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
            <p className="text-sm">{info or "Checking backup..."}</p>
            <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={save_backup} disabled={busy}>
                    <HugeiconsIcon icon={Download01Icon} strokeWidth={2} />{"Download backup"}
                </Button>
                <label className={"inline-flex cursor-pointer items-center gap-2 rounded-md border pt-2 pb-2 pl-3 pr-3 text-sm font-medium transition-colors hover:bg-muted " + ("pointer-events-none opacity-50" if busy else "")}>
                    <HugeiconsIcon icon={Upload01Icon} strokeWidth={2} className="size-4" />{"Restore from file"}
                    <input type="file" accept="application/json,.json" className="hidden" onChange={upload_file} />
                </label>
            </div>
            {message and <p className="text-xs text-muted-foreground">{message}</p>}
        </CardContent>
    </Card>;
}
