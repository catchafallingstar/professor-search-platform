import from react { useEffect }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { PlusSignIcon, Delete02Icon }
import from .ui.card { Card, CardHeader, CardTitle, CardDescription, CardContent }
import from .ui.button { Button }
import from .ui.badge { Badge }
import from .ui.input { Input }
import from .ui.table { Table, TableHeader, TableBody, TableRow, TableHead, TableCell }
sv import from ..services.queue {
    list_openalex_keys, add_openalex_key, remove_openalex_key, reset_openalex_key,
    KeyStatus, StepResult
}

def budget_label(k: KeyStatus) -> str {
    if k.remaining < 0 { return "Not used yet"; }
    if k.limit > 0 { return str(k.remaining) + " / " + str(k.limit); }
    return str(k.remaining) + " left";
}

def:pub OpenAlexKeys() -> JsxElement {
    has keys: list[KeyStatus] = [],
        draft: str = "",
        busy: bool = False,
        message: str = "",
        error: str = "";

    async def refresh() -> None {
        try { keys = await list_openalex_keys(); error = ""; } except Exception as e { error = "Could not load keys: " + str(e); }
    }

    async def add() -> None {
        if not draft.strip() { return; }
        busy = True;
        try {
            res: StepResult = await add_openalex_key(draft.strip());
            message = res.message;
            if res.ok { draft = ""; }
            await refresh();
        } except Exception as e { message = str(e); }
        busy = False;
    }

    async def remove(kid: str) -> None {
        try { res: StepResult = await remove_openalex_key(kid); message = res.message; await refresh(); } except Exception as e { message = str(e); }
    }

    async def reenable(kid: str) -> None {
        try { res: StepResult = await reset_openalex_key(kid); message = res.message; await refresh(); } except Exception as e { message = str(e); }
    }

    async can with entry { await refresh(); }

    useEffect(lambda {
        timer = setInterval(lambda { refresh(); }, 10000);
        return lambda { clearInterval(timer); };
    }, []);

    active = len([k for k in keys if k.state == "ACTIVE"]);

    return <Card className="shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between gap-4">
            <div className="flex flex-col gap-1">
                <CardTitle>{"OpenAlex keys"}</CardTitle>
                <CardDescription>{"Keys rotate automatically: the key with the most budget left is used first, a key is rested before it runs out, and the no-key pool is the last fallback."}</CardDescription>
            </div>
            <Badge variant={"secondary" if active > 0 else "outline"}>{str(active) + " of " + str(len(keys)) + " active"}</Badge>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                    value={draft}
                    placeholder="Paste an OpenAlex API key"
                    className="h-9 font-mono text-xs"
                    onChange={lambda (e: ChangeEvent) { draft = e.target.value; }}
                />
                <Button onClick={add} disabled={busy or not draft.strip()} className="shrink-0">
                    <HugeiconsIcon icon={PlusSignIcon} strokeWidth={2} />{"Verify and add key" if not busy else "Checking key"}
                </Button>
            </div>
            {message and <p className="text-xs text-muted-foreground">{message}</p>}
            {error and <p className="text-xs text-destructive">{error}</p>}
            <div className="overflow-x-auto rounded-lg border">
                <Table>
                    <TableHeader className="bg-muted/50">
                        <TableRow>
                            <TableHead>{"Key"}</TableHead>
                            <TableHead>{"Source"}</TableHead>
                            <TableHead>{"Status"}</TableHead>
                            <TableHead className="text-right">{"Daily budget"}</TableHead>
                            <TableHead className="text-right">{"Requests"}</TableHead>
                            <TableHead className="text-right">{""}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {for k in keys {
                            <TableRow key={k.id + str(k.order)}>
                                <TableCell className="font-mono text-xs">{k.id}</TableCell>
                                <TableCell className="text-xs text-muted-foreground">{k.source}</TableCell>
                                <TableCell>
                                    <div className="flex flex-col gap-0.5">
                                        <Badge variant={"default" if k.state == "ACTIVE" else ("destructive" if k.state == "INVALID" else "outline")}>
                                            {"Active" if k.state == "ACTIVE" else ("Invalid" if k.state == "INVALID" else "Resting " + str(k.cooldown_minutes) + " min")}
                                        </Badge>
                                        {k.last_error and <span className="text-xs text-muted-foreground">{k.last_error}</span>}
                                    </div>
                                </TableCell>
                                <TableCell className="text-right text-xs tabular-nums">{budget_label(k)}</TableCell>
                                <TableCell className="text-right text-xs tabular-nums">{str(k.uses)}</TableCell>
                                <TableCell className="text-right">
                                    <div className="flex justify-end gap-1">
                                        {k.state != "ACTIVE" and <Button variant="ghost" size="sm" onClick={lambda { reenable(k.id); }}>{"Re-enable"}</Button>}
                                        {k.source == "Pipeline page" and <Button variant="ghost" size="sm" onClick={lambda { remove(k.id); }}>
                                            <HugeiconsIcon icon={Delete02Icon} strokeWidth={2} />
                                        </Button>}
                                    </div>
                                </TableCell>
                            </TableRow>
                        }}
                    </TableBody>
                </Table>
            </div>
        </CardContent>
    </Card>;
}
