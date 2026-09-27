"""Priority queue + auto-worker.

Order: Michigan -> surrounding states -> top 30 national -> (once all are DONE,
incl. hiring) more US universities pulled from OpenAlex. f"process_next_step"
does one small unit of work; the background worker thread and the admin page
both call it.
"""

import os;
import threading;
import time;
import from services.models {
    DataRoot, Institution, Professor, FacultyPublicationAnchor, HasInstitution,
    HasProfessor, HasAnchor, HasHiringSignal, HasGrant, Authored,
    get_data_root, now_iso, normalize_name, find_institution
}
import from services.openalex { list_top_us_institutions, short_id }
import from services.crawler {
    PageData, fetch_page, extract_faculty, extract_profile, guess_directories, same_domain
}
import from services.pipeline { resolve_institution, process_professor, institution_awards, refresh_professor_cache, discover_grants, ingest_works }
import from services.persist { export_directory, restore_directory }
import services.universities as unis;
import services.fetchers as fx;
import from services.access { require_staff, worker_token, check_worker_token }

glob PRIORITY: list[dict] = unis.PRIORITY as list[dict],
     TIER_REASON: dict = unis.TIER_REASON as dict;

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
        hiring: int = 0;
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

glob MAX_PROFESSORS_PER_DEPARTMENT: int = 60,
     FINISHED: list[str] = ["DONE", "NO_FACULTY_FOUND", "FAILED"],
     WORKER: WorkerState = WorkerState();

def _recount(inst: Institution) -> None {
    # Heavy walk over professors -> papers/grants; run only when this university changes.
    profs = [inst ->:HasProfessor:->];
    inst.n_professors = len(profs);
    inst.n_processed = 0; inst.n_matched = 0; inst.n_unresolved = 0;
    inst.n_papers = 0; inst.n_grants = 0; inst.n_hiring = 0;
    for p in profs {
        if p.pipeline_done { inst.n_processed += 1; }
        if p.match_status == "MATCHED" { inst.n_matched += 1; }
        elif p.match_status == "UNRESOLVED" { inst.n_unresolved += 1; }
        inst.n_papers += len([p ->:Authored:->]);
        inst.n_grants += len([p ->:HasGrant:->]);
        if len([s for s in [p ->:HasHiringSignal:->] if s.quote]) > 0 { inst.n_hiring += 1; }
    }
}

def _status_for(inst: Institution) -> InstitutionStatus {
    # Cheap: reads cached counters only (the admin page polls this every few seconds).
    return InstitutionStatus(
        id=jid(inst), name=inst.name, city=inst.city, state=inst.state,
        openalex_institution_id=inst.openalex_institution_id,
        priority_tier=inst.priority_tier, priority_reason=inst.priority_reason,
        pipeline_state=inst.pipeline_state, pipeline_note=inst.pipeline_note,
        professors=inst.n_professors, processed=inst.n_processed, matched=inst.n_matched,
        unresolved=inst.n_unresolved, papers=inst.n_papers, grants=inst.n_grants, hiring=inst.n_hiring
    );
}

def _queue() -> list[Institution] {
    insts = [get_data_root() ->:HasInstitution:->];
    insts.sort(key=lambda (i: Institution) -> int { return i.priority_tier * 10000 + i.priority_rank; });
    return insts;
}

def _dirs_for(inst: Institution) -> list[list[str]] {
    for row in PRIORITY {
        if row["ipeds"] == inst.ipeds_id and len(row["dirs"]) > 0 {
            return row["dirs"] as list[list[str]];
        }
    }
    out: list[list[str]] = [];
    for g in guess_directories(inst.name, inst.official_website) {
        if g.url and same_domain(g.url, inst.official_website) {
            out.append([g.department, g.url]);
        }
    }
    return out;
}

def _crawl_faculty(inst: Institution) -> int {
    # Up to MAX_PROFESSORS_PER_DEPARTMENT per department (department = the directory's
    # department label), so large universities with many majors are covered.
    profs = [inst ->:HasProfessor:->];
    existing = [p.normalized_name for p in profs];
    per_dept: dict = {};
    for p in profs {
        per_dept[p.department] = int(per_dept.get(p.department, 0)) + 1;
    }
    added = 0;
    ts = now_iso();
    for d in _dirs_for(inst) {
        page = fetch_page(d[1]);
        if not page.ok { continue; }
        for f in extract_faculty(page, d[0]) {
            nn = normalize_name(f.name);
            dept = f.department or d[0];
            if nn in existing or int(per_dept.get(dept, 0)) >= MAX_PROFESSORS_PER_DEPARTMENT { continue; }
            p = Professor(
                name=f.name.strip(), normalized_name=nn, title=f.title or "Professor",
                department=dept, institution_id=jid(inst),
                faculty_url=f.profile_url or page.url, created_at=ts, updated_at=ts
            );
            inst +>:HasProfessor():+> p;
            existing.append(nn);
            per_dept[dept] = int(per_dept.get(dept, 0)) + 1;
            added += 1;
        }
    }
    return added;
}

def enrich_profile(p: Professor) -> None {
    if p.profile_extracted or not p.faculty_url { return; }
    p.profile_extracted = True;
    info = extract_profile(fetch_page(p.faculty_url), p.name);
    if info is None { return; }
    if info.lab_url and not p.lab_url { p.lab_url = info.lab_url; }
    if info.personal_url and not p.personal_url { p.personal_url = info.personal_url; }
    if info.orcid and not p.orcid { p.orcid = short_id(info.orcid); }
    have = [a.title.lower() for a in [p ->:HasAnchor:->]];
    ts = now_iso();
    for pub in info.publications {
        if not pub.title or pub.title.lower() in have or len(have) >= 3 { continue; }
        p +>:HasAnchor():+> FacultyPublicationAnchor(
            title=pub.title, doi=pub.doi, publication_year=pub.year, url=pub.url, created_at=ts
        );
        have.append(pub.title.lower());
    }
}

def _step_institution(inst: Institution) -> str {
    if inst.pipeline_state == "QUEUED" {
        resolve_institution(inst);
        inst.pipeline_state = "CRAWLING";
        return f"{inst.name}: OpenAlex institution {inst.openalex_institution_id or 'not found'}";
    }
    if inst.pipeline_state == "CRAWLING" {
        added = _crawl_faculty(inst);
        if len([inst ->:HasProfessor:->]) == 0 {
            inst.pipeline_state = "NO_FACULTY_FOUND";
            inst.pipeline_note = "No faculty directory could be read. Add directory URLs in services/universities.py.";
            return f"{inst.name}: no faculty directory readable";
        }
        inst.pipeline_state = "PROCESSING";
        return f"{inst.name}: imported {added} professors from official faculty pages";
    }
    if inst.pipeline_state == "PROCESSING" {
        resolve_institution(inst);
        todo = [p for p in [inst ->:HasProfessor:->] if not p.pipeline_done];
        if len(todo) > 0 {
            enrich_profile(todo[0]);
            return process_professor(todo[0], inst);
        }
        inst.pipeline_state = "UNIVERSITY_AWARDS";
        return f"{inst.name}: all professors processed (incl. hiring)";
    }
    if inst.pipeline_state == "UNIVERSITY_AWARDS" {
        extra = institution_awards(inst) if os.environ.get("GRANTS_ENABLED", "0") == "1" else 0;
        inst.pipeline_state = "DONE";
        inst.pipeline_note = f"{extra} extra grant links from institution-wide awards.";
        return f"{inst.name}: done";
    }
    return f"{inst.name}: {inst.pipeline_state}";
}

def _expand_queue() -> int {
    data = get_data_root();
    insts = [data ->:HasInstitution:->];
    known_ids = [i.openalex_institution_id for i in insts if i.openalex_institution_id];
    known_names = [i.name.lower() for i in insts];
    added = 0;
    ts = now_iso();
    for page_no in [1, 2, 3, 4, 5, 6] {
        for r in list_top_us_institutions(page_no) {
            oid = short_id(str(r.get("id", "") or ""));
            name = str(r.get("display_name", "") or "");
            if not name or oid in known_ids or name.lower() in known_names { continue; }
            geo = r.get("geo", {}) or {};
            inst = Institution(
                name=name, city=str(geo.get("city", "") or ""), state=str(geo.get("region", "") or ""),
                official_website=str(r.get("homepage_url", "") or ""), openalex_institution_id=oid,
                ror_id=short_id(str(r.get("ror", "") or "")), priority_tier=4,
                priority_rank=len(known_names), priority_reason=str(TIER_REASON[4]),
                created_at=ts, updated_at=ts
            );
            data +>:HasInstitution():+> inst;
            known_ids.append(oid);
            known_names.append(name.lower());
            added += 1;
            if added >= 25 { return added; }
        }
    }
    return added;
}

def _load_priority() -> int {
    data = get_data_root();
    known = [i.ipeds_id for i in [data ->:HasInstitution:->]];
    ts = now_iso();
    rank = 0;
    added = 0;
    for row in PRIORITY {
        rank += 1;
        if row["ipeds"] in known { continue; }
        tier = int(row["tier"]);
        data +>:HasInstitution():+> Institution(
            name=str(row["name"]), city=str(row["city"]), state=str(row["state"]),
            official_website=str(row["web"]), ipeds_id=str(row["ipeds"]), ror_id=str(row["ror"]),
            priority_tier=tier, priority_rank=rank, priority_reason=str(TIER_REASON[tier]),
            created_at=ts, updated_at=ts
        );
        added += 1;
    }
    return added;
}

def _log(msg: str) -> None {
    WORKER.last_message = msg;
    WORKER.last_at = now_iso();
    WORKER.steps += 1;
    WORKER.log = ([WORKER.last_at[11:19] + "  " + msg] + WORKER.log)[:40];
    print(f"[pipeline] {msg}");
}

def _crawl_only_step() -> StepResult | None {
    # OpenAlex is rate-limited: keep importing faculty from university sites (no OpenAlex needed).
    for inst in _queue() {
        if inst.pipeline_state not in ["QUEUED", "CRAWLING"] { continue; }
        try {
            added = _crawl_faculty(inst);
            total = len([inst ->:HasProfessor:->]);
            if total == 0 {
                inst.pipeline_state = "NO_FACULTY_FOUND";
                inst.pipeline_note = "No faculty directory could be read. Add directory URLs in services/universities.py.";
                return StepResult(message=f"{inst.name}: no faculty directory readable", institution=inst.name);
            }
            # OpenAlex institution id is resolved later when PROCESSING starts.
            inst.pipeline_state = "PROCESSING";
            _recount(inst);
            return StepResult(message=f"{inst.name}: imported {added} professors (OpenAlex matching waits for rate limit)", institution=inst.name);
        } except Exception as e {
            inst.pipeline_state = "FAILED";
            inst.pipeline_note = str(e)[:300];
            return StepResult(ok=False, message=f"{inst.name}: crawl failed ({str(e)[:200]})", institution=inst.name);
        }
    }
    return None;
}

def _step_unlocked() -> StepResult {
    if len([get_data_root() ->:HasInstitution:->]) == 0 {
        n = _load_priority();
        return StepResult(message=f"Loaded {n} priority universities.");
    }
    if bool((fx.rate_limit_status() as dict)["limited"]) {
        r = _crawl_only_step();
        if r is not None { return r; }
        return StepResult(ok=False, message=str((fx.rate_limit_status() as dict)["message"]));
    }
    for inst in _queue() {
        if inst.pipeline_state in FINISHED { continue; }
        try {
            msg = _step_institution(inst);
            _recount(inst);
            return StepResult(message=msg, institution=inst.name);
        } except fx.RateLimited as rl {
            # Keep state as-is; the same professor is retried after the limit resets.
            r = _crawl_only_step();
            if r is not None { return r; }
            return StepResult(ok=False, message=str(rl), institution=inst.name);
        } except Exception as e {
            inst.pipeline_state = "FAILED";
            inst.pipeline_note = str(e)[:300];
            return StepResult(ok=False, message=f"{inst.name}: failed ({str(e)[:200]})", institution=inst.name);
        }
    }
    added = _expand_queue();
    if added == 0 {
        return StepResult(message="All universities processed.", all_done=True);
    }
    return StepResult(message=f"Priority universities complete. Queued {added} more US universities.");
}

def step_once() -> StepResult {
    fx.acquire();
    try {
        return _step_unlocked();
    } finally {
        fx.release();
    }
}

# ---- endpoints ----
# Everything the Pipeline page calls is def:priv + require_staff() (enforced on the server).
# The background worker is not a user: it calls process_next_step with the internal token.

# Writes to the shared database must run in the shared (guest) context: a signed-in
# staff request runs on the staff member's own root and is denied write access to
# shared nodes. So each staff write endpoint checks require_staff() and then asks the
# internal endpoint (anonymous + worker token) to do the work.

def _as_worker(action: str, arg: str = "") -> StepResult {
    try {
        r = fx.call_internal("internal_action", {"action": action, "arg": arg}) as dict;
        return StepResult(ok=bool(r.get("ok", True)), message=str(r.get("message", "")));
    } except Exception as e {
        return StepResult(ok=False, message=f"{action} failed: {str(e)[:200]}");
    }
}

def:pub internal_action(worker_token: str = "", action: str = "", arg: str = "") -> StepResult {
    if not check_worker_token(worker_token) {
        return StepResult(ok=False, message="Forbidden");
    }
    if action == "load_priority" {
        n = _load_priority();
        return StepResult(message=f"Loaded {n} universities into the queue.");
    }
    if action == "step" {
        return _run_step();
    }
    if action == "recount" {
        for inst in _queue() { _recount(inst); }
        return StepResult(message="Counters refreshed.");
    }
    if action == "retry_unreadable" {
        n = 0;
        for inst in _queue() {
            if inst.pipeline_state in ["NO_FACULTY_FOUND", "FAILED"] {
                inst.pipeline_state = "QUEUED";
                inst.pipeline_note = "";
                n += 1;
            }
        }
        msg = f"Re-queued {n} universities for another faculty crawl.";
        _log(msg);
        return StepResult(message=msg);
    }
    if action == "restore" {
        msg = restore_directory();
        for inst in _queue() { _recount(inst); }
        _log(msg);
        return StepResult(message=msg);
    }
    if action == "snapshot" {
        ok = export_directory(True);
        return StepResult(ok=ok, message=("Snapshot saved." if ok else "Nothing to save yet."));
    }
    if action == "reindex" {
        # Backfill search text + research fields for professors stored before search was widened.
        # Only professors missing their search text (e.g. just restored); cheap on a normal boot.
        n = 0;
        for inst in _queue() {
            for p in [inst ->:HasProfessor:->] {
                if not p.search_text {
                    refresh_professor_cache(p);
                    n += 1;
                }
            }
        }
        if n > 0 { export_directory(True); }
        return StepResult(message=f"Re-indexed {n} professors for search.");
    }
    if action == "recrawl_all" {
        # Picks up newly added department directories / the per-department limit.
        # Existing professors keep their results; only new ones get processed.
        n = 0;
        for inst in _queue() {
            if inst.pipeline_state in ["PROCESSING", "UNIVERSITY_AWARDS", "DONE", "NO_FACULTY_FOUND"] {
                inst.pipeline_state = "CRAWLING";
                inst.pipeline_note = "";
                n += 1;
            }
        }
        msg = f"Re-crawling faculty directories for {n} universities (per-department limit {MAX_PROFESSORS_PER_DEPARTMENT}).";
        _log(msg);
        return StepResult(message=msg);
    }
    if action == "grant_check" {
        # One-off grant pass (grants are off in the automatic pipeline). For every MATCHED
        # professor: re-read recent works for award ids, fetch each award, link only when the
        # professor is listed as PI / Co-PI / investigator. arg = max professors (default 25).
        limit = int(arg) if arg.isdigit() else 25;
        done = 0; linked = 0;
        for inst in _queue() {
            for p in [inst ->:HasProfessor:->] {
                if done >= limit { break; }
                if p.match_status != "MATCHED" or not p.openalex_author_id or p.last_grant_update { continue; }
                try {
                    linked += discover_grants(p, inst, ingest_works(p));
                } except fx.RateLimited {
                    return StepResult(ok=False, message=f"Rate limited after {done} professors ({linked} grants linked). Run again later.");
                }
                p.last_grant_update = p.last_grant_update or now_iso();
                refresh_professor_cache(p);
                done += 1;
            }
            _recount(inst);
        }
        export_directory(True);
        msg = f"Grant check: {done} professors checked, {linked} grants linked.";
        _log(msg);
        return StepResult(message=msg);
    }
    if action == "reset_institution" {
        inst = find_institution(arg);
        if inst is None { return StepResult(ok=False, message="Institution not found."); }
        inst.pipeline_state = "QUEUED";
        inst.pipeline_note = "";
        for p in [inst ->:HasProfessor:->] { p.pipeline_done = False; }
        _recount(inst);
        return StepResult(message=f"{inst.name} re-queued.");
    }
    return StepResult(ok=False, message="Unknown action");
}

def:priv load_priority_universities() -> list[InstitutionStatus] {
    require_staff();
    r = _as_worker("load_priority");
    if not r.ok { raise RuntimeError(r.message); }
    return [_status_for(i) for i in _queue()];
}

def:priv get_pipeline_status() -> list[InstitutionStatus] {
    require_staff();
    return [_status_for(i) for i in _queue()];
}

def:priv download_snapshot() -> str {
    # Staff: returns the current directory as JSON text (to save a copy outside the sandbox).
    require_staff();
    _as_worker("snapshot");
    import services.snapshot as snapmod;
    d = snapmod.load();
    import json;
    return json.dumps(d) if d is not None else "";
}

def:priv upload_snapshot(content: str) -> StepResult {
    # Staff: restore the directory from a previously downloaded snapshot file.
    require_staff();
    import json;
    try {
        parsed = json.loads(content);
    } except Exception {
        return StepResult(ok=False, message="That file is not a valid snapshot.");
    }
    if not isinstance(parsed, dict) or "institutions" not in parsed {
        return StepResult(ok=False, message="That file is not a valid snapshot.");
    }
    import services.snapshot as snapmod;
    snapmod.save(parsed as dict, True);
    return _as_worker("restore");
}

def:priv snapshot_info() -> StepResult {
    require_staff();
    import services.snapshot as snapmod;
    i = snapmod.info() as dict;
    if not bool(i["exists"]) {
        return StepResult(ok=False, message="No snapshot saved yet.");
    }
    return StepResult(message=f"Last saved {str(i['saved_at'])[:16].replace('T', ' ')} UTC: {i['institutions']} universities, {i['professors']} professors.");
}

def:priv run_step_as_staff() -> StepResult {
    require_staff();
    return _as_worker("step");
}

def:pub process_next_step(worker_token: str = "") -> StepResult {
    if not check_worker_token(worker_token) {
        return StepResult(ok=False, message="Forbidden");
    }
    return _run_step();
}

def _run_step() -> StepResult {
    r = step_once();
    limited = bool((fx.rate_limit_status() as dict)["limited"]);
    if limited and not r.ok and r.message.startswith("All OpenAlex credentials") {
        # Waiting on a rate limit is expected, not an error: note it once per wait period.
        if bool(fx.first_rate_notice()) {
            _log("Waiting for OpenAlex rate limit to reset (faculty crawling is done for now). Add keys on this page to continue sooner.");
        }
        return r;
    }
    fx.clear_rate_notice();
    _log(r.message);
    try {
        export_directory(False);   # throttled; keeps data/directory.json current
    } except Exception as e {
        print(f"[persist] snapshot failed: {str(e)[:200]}");
    }
    return r;
}

def:priv get_worker_state() -> WorkerState {
    require_staff();
    WORKER.running = bool(fx.worker_running());
    return WORKER;
}

def:priv get_config_status() -> ConfigStatus {
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

def:priv set_worker(running: bool) -> WorkerState {
    # The Python worker thread calls /function/process_next_step (with the internal token),
    # so every step runs in a normal request context and its graph writes persist.
    require_staff();
    if running {
        if not bool(fx.llm_configured()) {
            _log("No LLM key: importing faculty + OpenAlex papers/grants now. Hiring checks run once an LLM key (ANTHROPIC_API_KEY + LLM_MODEL, or OPENAI_API_KEY) is added.");
        }
        fx.start_worker();
        _log("Auto-processing started.");
    } else {
        fx.stop_worker();
        _log("Auto-processing paused.");
    }
    WORKER.running = bool(fx.worker_running());
    return WORKER;
}

def:priv recount_all() -> StepResult {
    require_staff();
    return _as_worker("recount");
}

def:priv retry_unreadable() -> StepResult {
    # Re-queue universities whose directories could not be read (e.g. after new URLs were added).
    require_staff();
    return _as_worker("retry_unreadable");
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

def:priv list_openalex_keys() -> list[KeyStatus] {
    # Masked (...last4) status of every credential in rotation order; full keys never leave the server.
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

def:priv remove_openalex_key(key_id: str) -> StepResult {
    require_staff();
    r = fx.remove_key(key_id) as dict;
    _log(str(r["message"]));
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

def:priv reset_openalex_key(key_id: str) -> StepResult {
    require_staff();
    r = fx.reset_key(key_id) as dict;
    _log(str(r["message"]));
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

def:priv add_openalex_key(key: str) -> StepResult {
    # Adds a key to the rotating pool immediately (no restart), verified with one live call.
    require_staff();
    r = fx.add_key(key) as dict;
    _log(str(r["message"]));
    return StepResult(ok=bool(r["ok"]), message=str(r["message"]));
}

with entry {
    # Worker authenticates to process_next_step with this token; then resume if it was running.
    fx.set_worker_token(worker_token());
    # Reload the saved directory into an empty graph (sandbox restarts wipe .jac/data),
    # then resume the worker. Runs via the internal endpoint once the API is up.
    fx.restore_then_resume();
}

def:priv reset_institution(institution_id: str) -> InstitutionStatus | None {
    require_staff();
    r = _as_worker("reset_institution", institution_id);
    if not r.ok { return None; }
    inst = find_institution(institution_id);
    return _status_for(inst) if inst is not None else None;
}
