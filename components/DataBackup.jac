import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Download01Icon, Upload01Icon }
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from ..services.queue {
    download_snapshot,
    upload_snapshot,
    snapshot_info,
    StepResult
}

def:pub DataBackup() -> JsxElement {
    has info: str = "",
        busy: bool = False,
        message: str = "",
        file_url: str = "",
        file_name: str = "";

    async def refresh() -> None {
        try {
            r: StepResult = await snapshot_info();
            info = r.message;
        } except Exception as e {
            info = "Could not read backup status: " + str(e);
        }
    }

    async def save_backup() -> None {
        # Building the file can take a while, and browsers drop a download that is
        # triggered after a long wait (no longer tied to the click). So prepare the file,
        # then show a real "Save" link the user clicks. Also works inside the IDE frame.
        busy = True;
        message = "Preparing backup...";
        body: str = "";
        try {
            body = await download_snapshot();
        } except Exception as e {
            message = "Backup failed: " + str(e);
        }
        busy = False;
        if not body {
            message = message
                if message.startswith("Backup failed")
                else "Nothing to download yet.";
            return;
        }
        if file_url {
        }  # jac:ignore[E1032]
        blob: any = new(Blob, [body], {"type": "application/json"});  # jac:ignore[E1053]
        stamp: any = new(Date).toISOString();  # jac:ignore[E1053]
        file_url = str(globalThis.URL.createObjectURL(blob));  # jac:ignore[E1032]
        file_name = "professor-atlas-" + str(stamp)[:16].replace(":", "-") + ".json";
        message = f"Backup ready ({int(
            len(body) / 1024
        )} KB). Click Save file. If nothing happens, open the preview in its own tab first.";
        await refresh();
    }

    async def upload_file(e: any) -> None {
        f: any = e.target.files[0] if e.target.files else None;
        if not f {
            return;
        }
        busy = True;
        message = "Uploading and restoring...";
        text: str = "";
        try {
            text = await f.text();
            r: StepResult = await upload_snapshot(text);
            message = r.message;
        } except Exception as ex {
            message = "Restore failed: " + str(ex);
        }
        busy = False;
        e.target.value = "";
        await refresh();
    }

    async can with entry {
        await refresh();
    }

    return
        <Card className="shadow-sm">
            <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div className="flex flex-col gap-1">
                    <CardTitle>{"Backup"}</CardTitle>
                    <CardDescription>
                        {"Everything is saved automatically as the pipeline runs. Download a copy to keep it safe outside the preview; upload it to restore into an empty database."}
                    </CardDescription>
                </div>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
                <p className="text-sm">{info or "Checking backup..."}</p>
                <div className="flex flex-wrap gap-2">
                    <Button variant="outline" onClick={save_backup} disabled={busy}>
                        <HugeiconsIcon icon={Download01Icon} strokeWidth={2}/>
                        {"Prepare backup" if not busy else "Preparing..."}
                    </Button>
                    {file_url
                        and <a
                            href={file_url}
                            download={file_name}
                            target="_blank"
                            rel="noopener"
                            className="inline-flex items-center gap-2 rounded-md bg-primary pt-2 pb-2 pl-3 pr-3 text-sm font-medium text-primary-foreground hover:opacity-90"
                        >
                            <HugeiconsIcon
                                icon={Download01Icon}
                                strokeWidth={2}
                                className="size-4"
                            />
                            {"Save file"}
                        </a>}
                    <label
                        className={"inline-flex cursor-pointer items-center gap-2 rounded-md border pt-2 pb-2 pl-3 pr-3 text-sm font-medium transition-colors hover:bg-muted "
                            + ("pointer-events-none opacity-50" if busy else "")}
                    >
                        <HugeiconsIcon
                            icon={Upload01Icon}
                            strokeWidth={2}
                            className="size-4"
                        />
                        {"Restore from file"}
                        <input
                            type="file"
                            accept="application/json,.json"
                            className="hidden"
                            onChange={upload_file}
                        />
                    </label>
                </div>
                {message and <p className="text-xs text-muted-foreground">{message}</p>}
            </CardContent>
        </Card>;
}
