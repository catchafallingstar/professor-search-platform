"""Staff pipeline endpoints. The work itself is Python (services/pipe.py) writing straight
to MongoDB (services/store.py), so nothing depends on the sandbox disk."""

import services.store as st;
import services.pipe as pipe;
import services.fetchers as fx;
import from services.access { require_staff, worker_token, check_worker_token }
import services.ai as ai;

obj ConfigStatus {
    has openalex_ok: bool = False,
        openalex_mailto: bool = False,
        openalex_key: bool = False,
        llm_key: bool = False,
        rate_limited: bool = False,
        rate_limit_message: str = "",
        keys_total: int = 0,
        keys_available: int = 0,
        keys_invalid: int = 0;
}

obj InstitutionStatus {
    has id: str = "",
        name: str = "",
        city: str = "",
        state: str = "",
        openalex_institution_id: str = "",
        priority_tier: int = 4,
        priority_reason: str = "",
        pipeline_state: str = "",
        pipeline_note: str = "",
        professors: int = 0,
        processed: int = 0,
        matched: int = 0,
        unresolved: int = 0,
        papers: int = 0,
        grants: int = 0,
        hiring: int = 0,
        coverage_state: str = "",
        coverage_units: int = 0,
        coverage_readable: int = 0,
        coverage_missing: int = 0;
}

obj StepResult {
    has ok: bool = True,
        message: str = "",
        institution: str = "",
        all_done: bool = False;
}

obj WorkerState {
    has running: bool = False,
        steps: int = 0,
        last_message: str = "",
        last_at: str = "",
        log: list[str] = [];
}

obj DatabaseUpgradeState {
    has done: bool = False,
        status: str = "PENDING",
        started_at: str = "",
        ran_at: str = "",
        message: str = "";
}

obj KeyStatus {
    has id: str = "",
        source: str = "",
        state: str = "",
        remaining: int = -1,
        limit: int = -1,
        cooldown_minutes: int = 0,
        uses: int = 0,
        last_error: str = "",
        order: int = 0;
}

def _status(r: dict) -> InstitutionStatus {
    return InstitutionStatus(
        id=str(r.get("id", "")), name=str(r.get("name", "")), city=str(r.get("city", "")),
        state=str(r.get("state", "")), openalex_institution_id=str(r.get("openalex_institution_id", "")),
        priority_tier=int(r.get("priority_tier") or 4), priority_reason=str(r.get("priority_reason", "")),
        pipeline_state=str(r.get("pipeline_state", "")), pipeline_note=str(r.get("pipeline_note", "")),
        professors=int(r.get("n_professors") or 0), processed=int(r.get("n_processed") or 0),
        matched=int(r.get("n_matched") or 0), unresolved=int(r.get("n_unresolved") or 0),
        papers=int(r.get("n_papers") or 0), grants=int(r.get("n_grants") or 0), hiring=int(r.get("n_hiring") or 0),
        coverage_state=str(r.get("coverage_state") or ""),
        coverage_units=int((r.get("coverage") or {}).get("units") or 0),
        coverage_readable=int((r.get("coverage") or {}).get("readable_units") or 0),
        coverage_missing=len((r.get("coverage") or {}).get("missing") or []) + len((r.get("coverage") or {}).get("empty_directories") or [])
    );
}

def _worker_state() -> WorkerState {
    s = pipe.log_state() as dict;
    return WorkerState(
        running=bool(pipe.running()), steps=int(s["steps"]), last_message=str(s["last"]),
        last_at=str(s["at"]), log=list(s["lines"])
    );
}

def _safe_step() -> StepResult {
    try {
        msg = str(pipe.step());
        if msg { pipe.log(msg); }
        return StepResult(message=msg or "Nothing left to do.", all_done=(msg == ""));
    } except fx.RateLimited as e {
        return StepResult(ok=False, message="All OpenAlex credentials are resting: " + str(e));
    } except Exception as e {
        return StepResult(ok=False, message="Step failed: " + str(e)[:200]);
    }
}

# ---- staff endpoints (require_staff is enforced on the server) ----

def:protect load_priority_universities() -> list[InstitutionStatus] {
    require_staff();
    r = pipe.load_ipeds();
    pipe.log(f"Loaded {r[1]} research universities from IPEDS {r[0]} ({r[2]} new).");
    return [_status(i) for i in (pipe.queue() as list[dict])];
}

def:protect get_pipeline_status() -> list[InstitutionStatus] {
    require_staff();
    return [_status(i) for i in (pipe.queue() as list[dict])];
}

def:protect run_step_as_staff() -> StepResult {
    require_staff();
    return _safe_step();
}

def:protect get_worker_state() -> WorkerState {
    require_staff();
    return _worker_state();
}

def:protect set_worker(running: bool) -> WorkerState {
    require_staff();
    if running {
        pipe.start();
        pipe.log("Auto-processing started.");
    } else {
        pipe.stop();
        pipe.log("Auto-processing paused.");
    }
    return _worker_state();
}

obj ReviewItem {
    has id: str = "",
        task_type: str = "",
        reason: str = "",
        detail: str = "",
        university: str = "",
        professor: str = "",
        professor_id: str = "",
        department: str = "",
        source_url: str = "",
        attempted_at: str = "",
        queue_status: str = "",
        last_retry_result: str = "",
        last_retry_at: str = "";
}

obj QueueJob {
    has id: str = "",
        kind: str = "",
        status: str = "",
        professor: str = "",
        task: str = "",
        result: str = "",
        when: str = "";
}

def:protect list_staff_review(limit: int = 200) -> list[ReviewItem] {
    require_staff();
    out: list[ReviewItem] = [];
    for d in (st.staff_review_items(limit) as list[dict]) {
        out.append(ReviewItem(
            id=str(d.get("id", "")), task_type=str(d.get("task_type", "")), reason=str(d.get("reason", "")),
            detail=str(d.get("last_error") or ""), university=str(d.get("university") or ""),
            professor=str(d.get("professor") or ""), professor_id=str(d.get("professor_id") or ""),
            department=str(d.get("department") or ""), source_url=str(d.get("source_url") or ""),
            attempted_at=str(d.get("attempted_at") or ""), queue_status=str(d.get("queue_status") or ""),
            last_retry_result=str(d.get("last_retry_result") or ""), last_retry_at=str(d.get("last_retry_at") or "")
        ));
    }
    return out;
}

def:protect list_recent_jobs(limit: int = 10) -> list[QueueJob] {
    require_staff();
    return [QueueJob(id=str(j["id"]), kind=str(j["kind"]), status=str(j["status"]), professor=str(j["professor"]),
                     task=str(j["task"]), result=str(j["result"]), when=str(j["when"]))
            for j in (pipe.recent_jobs(int(limit)) as list[dict])];
}

def:protect requeue_staff_review() -> StepResult {
    require_staff();
    r = pipe.queue_review_retries("") as tuple;
    n = int(r[0]);
    already = int(r[1]);
    started = "";
    if not pipe.running() {
        # queued jobs only run while the worker is on
        pipe.start();
        pipe.log("Auto-processing started (staff review re-queue).");
        started = " Auto-processing was paused and has been started.";
    }
    plural: str = "" if n == 1 else "s";
    waiting_note: str = f" ({already} already waiting)" if already else "";
    return StepResult(message=f"Re-queued {n} item{plural} at the top of the queue{waiting_note}. Fixed items leave this list; the rest show their latest result.{started}");
}

def:protect resolve_staff_review(item_id: str) -> StepResult {
    require_staff();
    st.resolve_staff_review(item_id);
    return StepResult(message="Marked as handled.");
}

def:protect reset_institution(institution_id: str) -> InstitutionStatus | None {
    require_staff();
    st.update_institution(institution_id, {"pipeline_state": "QUEUED", "pipeline_note": ""});
    r = st.get_institution(institution_id);
    return _status(r as dict) if r is not None else None;
}

def:protect retry_unreadable() -> StepResult {
    require_staff();
    n = int(pipe.requeue());
    return StepResult(message=f"Re-queued {n} universities for another faculty crawl.");
}

def:protect recount_all() -> StepResult {
    require_staff();
    for i in (pipe.queue() as list[dict]) { st.recount(str(i["id"])); }
    return StepResult(message="Counters refreshed.");
}

def:protect get_config_status() -> ConfigStatus {
    require_staff();
    d = fx.config_status() as dict;
    rl = fx.rate_limit_status() as dict;
    return ConfigStatus(
        openalex_ok=bool(d["openalex_ok"]), openalex_mailto=bool(d["openalex_mailto"]),
        openalex_key=bool(d["openalex_key"]), llm_key=bool(d["llm_key"]),
        rate_limited=bool(rl["limited"]), rate_limit_message=str(rl["message"]),
        keys_total=int(d["openalex_keys_total"]), keys_available=int(d["openalex_keys_available"]),
        keys_invalid=int(d["openalex_keys_invalid"])
    );
}

def:protect check_llm() -> StepResult {
    # Is the LLM (e.g. Ollama on the owner's computer, via its tunnel URL) reachable right now?
    require_staff();
    r = fx.llm_reachable();
    return StepResult(ok=bool(r[0]), message=str(r[1]));
}

def:protect run_maintenance_now() -> StepResult {
    # Start the monthly refresh immediately (faculty lists, papers, subfields, hiring).
    require_staff();
    n = int(pipe.start_maintenance());
    pipe.log(f"Maintenance started by staff: re-checking {n} universities.");
    return StepResult(message=f"Maintenance started: re-checking {n} universities.");
}

def:protect get_database_upgrade_state() -> DatabaseUpgradeState {
    require_staff();
    import services.upgrade_runner as upgr;
    d = upgr.state() as dict;
    status = str(d.get("status") or "PENDING");
    done = bool(d.get("done"));
    ran_at = str(d.get("ran_at") or "");
    started_at = str(d.get("started_at") or "");
    err = str(d.get("error") or "");
    msg = "";
    if done {
        msg = "Database upgrade completed" + (" at " + ran_at if ran_at else "") + ".";
    } elif status == "RUNNING" {
        msg = "Database upgrade is running" + (" (started " + started_at + ")" if started_at else "") + ".";
    } elif status == "FAILED" {
        msg = "Database upgrade failed" + (": " + err if err else ". You can safely run it again.");
    } else {
        msg = "Database upgrade has not been run yet.";
    }
    return DatabaseUpgradeState(done=done, status=status, started_at=started_at, ran_at=ran_at, message=msg);
}

def:protect run_database_upgrade() -> StepResult {
    # Start the one-time MongoDB repair in the background.
    require_staff();
    if pipe.running() {
        return StepResult(ok=False, message="Pause auto-processing before running the database upgrade.");
    }
    import services.upgrade_runner as upgr;
    before = upgr.state() as dict;
    if bool(before.get("done")) {
        return StepResult(message="Database upgrade already completed" + (" at " + str(before.get("ran_at")) if before.get("ran_at") else "") + ".");
    }
    after = upgr.start() as dict;
    if str(after.get("status") or "") == "RUNNING" {
        pipe.log("Database upgrade started by staff in the background.");
        return StepResult(message="Database upgrade started. Keep this page open; status updates automatically.");
    }
    if str(after.get("status") or "") == "FAILED" {
        return StepResult(ok=False, message="Database upgrade could not start: " + str(after.get("error") or "unknown error"));
    }
    return StepResult(message="Database upgrade status: " + str(after.get("status") or "PENDING"));
}

def:protect list_openalex_keys() -> list[KeyStatus] {
    require_staff();
    return [
        KeyStatus(
            id=str(r["id"]), source=str(r["source"]), state=str(r["state"]),
            remaining=int(r["remaining"]), limit=int(r["limit"]),
            cooldown_minutes=int(r["cooldown_minutes"]), uses=int(r["uses"]),
            last_error=str(r["last_error"]), order=int(r["order"])
        )
        for r in (fx.key_rows() as list[dict])
    ];
}

def:protect remove_openalex_key(key_id: str) -> StepResult {
    require_staff();
    r = fx.remove_key(key_id) as dict;
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

def:protect reset_openalex_key(key_id: str) -> StepResult {
    require_staff();
    r = fx.reset_key(key_id) as dict;
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

def:protect add_openalex_key(key: str) -> StepResult {
    require_staff();
    r = fx.add_key(key) as dict;
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

# ---- backup (MongoDB is the live store; this is an optional extra copy) ----

def:protect download_snapshot() -> str {
    require_staff();
    import services.backup as bk;
    return str(bk.export_json());
}

def:protect upload_snapshot(content: str) -> StepResult {
    require_staff();
    import services.backup as bk;
    r = bk.import_json(content) as dict;
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

def:protect snapshot_info() -> StepResult {
    require_staff();
    o = st.overview() as dict;
    return StepResult(message=f"Live in MongoDB: {o['institutions']} universities, {o['professors']} professors, {o['papers']} papers.");
}

# ---- internal (background tooling authenticates with the worker token) ----

def:pub process_next_step(worker_token: str = "") -> StepResult {
    if not check_worker_token(worker_token) {
        return StepResult(ok=False, message="Forbidden");
    }
    return _safe_step();
}

def:pub internal_action(worker_token: str = "", action: str = "", arg: str = "") -> StepResult {
    if not check_worker_token(worker_token) {
        return StepResult(ok=False, message="Forbidden");
    }
    if action == "step" { return _safe_step(); }
    if action == "load_priority" {
        r = pipe.load_ipeds();
        return StepResult(message=f"Loaded {r[1]} research universities from IPEDS {r[0]} ({r[2]} new).");
    }
    if action == "retry_unreadable" { return StepResult(message=f"Re-queued {pipe.requeue()} universities."); }
    if action == "retry_staff_review" {
        n = (pipe.queue_review_retries(arg) as tuple)[0];
        if not pipe.running() { pipe.start(); pipe.log("Auto-processing started (staff review re-queue)."); }
        return StepResult(message=f"Queued {n} review retries; worker running.");
    }
    if action == "queue_grant_check" { return StepResult(message="Queued " + str(pipe.queue_test_job("GRANT_CHECK", arg))); }
    if action == "queue_llm_identity" { return StepResult(message="Queued " + str(pipe.queue_test_job("LLM_IDENTITY", arg))); }
    if action == "recrawl_all" { return StepResult(message=f"Re-crawling {pipe.recrawl_all()} universities."); }
    if action == "ai_status" {
        return StepResult(message=f"chain={ai.chain()} resting={ai.resting()}");
    }
    return StepResult(ok=False, message="Unknown action");
}

with entry {
    fx.set_worker_token(worker_token());
    fx.restore_then_resume();
}
