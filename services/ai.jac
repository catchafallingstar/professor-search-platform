"""Every AI task in the pipeline, with a three-model OpenRouter fallback chain.

Jac decides everything (what to fetch, what to accept); the model only answers semantic
questions about text we already fetched, or proposes candidate URLs that Jac then verifies.

Fallback rules (per call):
  model 1 -> technical/invalid failure -> retry once -> model 2 -> retry once -> model 3 -> retry once
  -> STAFF_REVIEW (recorded by the caller; never turned into "no faculty" / "no signal").
A valid empty result or a valid UNCERTAIN is a real answer and is NOT escalated
(except UNCERTAIN on faculty extraction, where trying the next model is worthwhile).

How the model is switched: every f"by llm" function below is f"by cur()", and f"cur" is a
module global that run_task() points at the next model in the chain before each attempt.
"""

import os;
import time;
import threading;
import from jaclang.byllm.lib { Model }

# ---------- model chain ----------

glob DEFAULT_CHAIN: list[str] = [
    # thinkingmachines/inkling:free was removed: OpenRouter rejects it (403, "only available on agentic harnesses")
    "openrouter/nvidia/nemotron-3.5-lightning:free",
    "openrouter/nvidia/nemotron-3-super-120b-a12b"
];

def chain() -> list[str] {
    # LLM_MODELS overrides the chain (comma separated, LiteLLM names). A local Ollama can be
    # put first, e.g. "ollama/qwen2.5:14b,openrouter/nvidia/nemotron-3.5-lightning:free".
    raw = os.environ.get("LLM_MODELS", "").strip();
    if raw {
        return [m.strip() for m in raw.split(",") if m.strip()];
    }
    out: list[str] = [];
    single = os.environ.get("LLM_MODEL", "").strip();
    # A local model (Ollama behind a tunnel) goes FIRST: it has no credit or daily limit.
    if single and single.startswith("ollama") and ollama_base() {
        out.append(single);
    }
    if os.environ.get("OPENROUTER_API_KEY", "") {
        out = out + [m for m in DEFAULT_CHAIN if m not in out];
    } elif single and single not in out {
        out.append(single);
    }
    return out;
}

def page_cap(cloud_limit: int) -> int {
    # Local Ollama models usually run with a 4096-token context: a long page overflows it and
    # every attempt fails. ~6000 chars of page text leaves room for the prompt and the answer.
    models = chain();
    if models and models[0].startswith("ollama") {
        return min(cloud_limit, int(os.environ.get("OLLAMA_PAGE_CHARS", "6000") or 6000));
    }
    return cloud_limit;
}

def configured() -> bool {
    return len(chain()) > 0;
}

import services.ai_lock as ai_lock;

glob _MODELS: dict[str, any] = {},
     cur: any = None,
     _LOCK: threading.Lock = ai_lock.LOCK;

def ollama_base() -> str {
    # Quick tunnels get a new URL on every restart: read .env each time so a pasted new
    # URL takes effect without restarting the preview. The process env is updated too:
    # LiteLLM, the project-wide by llm() and llm_local.py all read OLLAMA_API_BASE from it,
    # and a stale value there sent every call to a dead tunnel (Cloudflare error 1033).
    try {
        with open(".env") as f {
            for line in f.read().splitlines() {
                if line.strip().startswith("OLLAMA_API_BASE=") {
                    v = line.split("=", 1)[1].strip().strip('"').strip("'").rstrip("/");
                    if v {
                        if os.environ.get("OLLAMA_API_BASE", "") != v {
                            os.environ["OLLAMA_API_BASE"] = v;
                            _REST.pop(local_name(), None);   # new tunnel: try it right away
                        }
                        return v;
                    }
                }
            }
        }
    } except Exception { }
    return os.environ.get("OLLAMA_API_BASE", "").strip().rstrip("/");
}

def local_name() -> str {
    single = os.environ.get("LLM_MODEL", "").strip();
    return single if single.startswith("ollama") else "";
}

# ---------- resting models (rate limit, out of credits, local model offline) ----------

glob _REST: dict[str, any] = {};    # model name -> [until_epoch, reason]

# The rest list is mirrored to a small file: hot reloads and worker threads that re-import this
# module would otherwise forget that OpenRouter hit its daily cap and call it again every job.
glob REST_FILE: str = "/tmp/atlas_ai_rest.json";

def _load_rest() -> None {
    try {
        import json;
        with open(REST_FILE) as f {
            data: dict[str, any] = json.load(f);
            for k in data.keys() {
                new_v: list[any] = data[k];
                old_v: list[any] = _REST.get(k, [0, ""]);
                if k not in _REST or float(str(new_v[0])) > float(str(old_v[0])) { _REST[k] = new_v; }
            }
        }
    } except Exception { }
}

def _save_rest() -> None {
    try {
        import json;
        with open(REST_FILE, "w") as f { json.dump(_REST, f); }
    } except Exception { }
}

def _rest_seconds(err: str, kind: str, name: str = "") -> list[any] {
    # [seconds, reason] or [] when the failure is not about availability
    t = err.lower();
    if name.startswith("ollama") {
        # Ollama has no billing or per-day cap of its own: those OpenRouter-style reasons below
        # (checked by matching stray digits like "402"/"429" in the error text) do not apply and
        # have mis-fired before on an unrelated tunnel hiccup, resting the local model for up to an
        # hour. A local model can only ever be "offline" (tunnel down / connection refused).
        if "cloudflare tunnel error" in t or "530" in t or "connection refused" in t or "timed out" in t
            or "failed to resolve" in t or "name or service not known" in t or "502" in t or "503" in t
            or "429" in t or "too many requests" in t {
            # short: both retries on this model already failed, but a quick tunnel blip clears fast
            return [20, "local model offline"];
        }
        return [];
    }
    if kind == "RATE_LIMITED" or "free-models-per-day" in t {
        # OpenRouter daily cap: rest until its X-RateLimit-Reset (ms epoch) when present
        idx = err.find("X-RateLimit-Reset");
        if idx >= 0 {
            digits = "".join([c for c in err[idx:idx + 60] if c.isdigit()]);
            if len(digits) >= 13 {
                secs: float = int(digits[:13]) / 1000 - time.time();
                if secs > 0 { return [min(secs, 86400.0), "daily limit reached"]; }
            }
        }
        return [900, "rate limited"];
    }
    if "requires more credits" in t or "insufficient credits" in t or "402" in t {
        return [3600, "out of credits"];
    }
    if "cloudflare tunnel error" in t or "530" in t or "connection refused" in t
        or "failed to resolve" in t or "name or service not known" in t {
        return [120, "local model offline"];
    }
    return [];
}

def resting() -> list[dict[str, any]] {
    now = time.time();
    out: list[dict[str, any]] = [];
    for m in list(_REST.keys()) {
        v: list[any] = _REST[m];
        until: float = float(v[0]);
        if until > now {
            out.append({"model": m, "seconds_left": int(until - now), "reason": v[1]});
        }
    }
    return out;
}

glob _CUR_NAME: str = "";

def ai_max_tokens() -> int {
    # f"by cur()" re-sets the model's call params on every call, so the Model(call_params=...)
    # limit is lost and OpenRouter reserves the model's full output (131072 tokens) and rejects
    # the request for credits. Pass the limit on every call instead.
    if _CUR_NAME.startswith("ollama") {
        return int(os.environ.get("OLLAMA_MAX_TOKENS", "700") or 700);
    }
    return 4000;
}

def _model(name: str) -> Model {
    key_name = name;
    if name.startswith("ollama") { key_name = name + "@" + ollama_base(); }
    if key_name not in _MODELS {
        cfg: dict[str, any] = {};
        key = "";
        if name.startswith("openrouter/") {
            key = os.environ.get("OPENROUTER_API_KEY", "");
        } elif name.startswith("ollama") {
            base = ollama_base();
            if base { cfg["base_url"] = base; }
        }
        params: dict[str, any] = {"temperature": 0.0, "max_tokens": 4000};
        if name.startswith("ollama") {
            # a local model behind a quick tunnel: Cloudflare drops requests after ~100 s, so keep replies short
            params["max_tokens"] = int(os.environ.get("OLLAMA_MAX_TOKENS", "700") or 700);
        }
        model_id = name;
        if name.startswith("ollama/") or name.startswith("ollama_chat/") {
            # chat route + thinking off: "thinking" models (qwen3) otherwise spend the reply on
            # reasoning text and return no JSON, which fails every typed answer
            model_id = "ollama_chat/" + name.split("/", 1)[1];
        }
        _MODELS[key_name] = Model(model_name=model_id, api_key=key, config=cfg, call_params=params);
    }
    return _MODELS[key_name];
}

# ---------- outcome of one AI task ----------

obj AiOutcome {
    has status: str = "",            # VALID_RESULT | VALID_EMPTY | UNCERTAIN | STAFF_REVIEW | RETRY_LATER
        value: any = None,
        model_used: str = "",
        attempt_number: int = 0,
        fallback_count: int = 0,
        models_attempted: list[str] = [],
        last_error: str = "",
        error_kind: str = "",        # TRANSPORT_ERROR | TIMEOUT | RATE_LIMITED | MODEL_ERROR | INVALID_OUTPUT | SCHEMA_ERROR
        local_model_error: str = "";  # the local model's own error, kept even after a cloud fallback overwrites last_error
}

def _kind(e: any) -> str {
    t = (type(e).__name__ + " " + str(e)).lower();
    if "timeout" in t or "timed out" in t { return "TIMEOUT"; }
    if ("rate" in t and "limit" in t) or "429" in t { return "RATE_LIMITED"; }
    if "conversion" in t or "json" in t or "parse" in t or "schema" in t or "validation" in t { return "SCHEMA_ERROR"; }
    if "connection" in t or "network" in t or "resolve" in t or "ssl" in t { return "TRANSPORT_ERROR"; }
    return "MODEL_ERROR";
}

def run_task(fn: any, args: list[any], check: any, escalate_uncertain: bool = False) -> AiOutcome {
    # fn(*args) -> raw value from a f"by cur()" function; check(raw) -> [status, value] where
    # status is VALID_RESULT / VALID_EMPTY / UNCERTAIN / INVALID_OUTPUT.
    out = AiOutcome();
    ollama_base();          # picks up a new tunnel URL pasted into .env (also clears its rest)
    _load_rest();
    models = chain();
    attempt = 0;
    skipped = 0;
    for (i, name) in enumerate(models) {
        rest: list[any] = _REST.get(name, [0, ""]);
        if float(rest[0]) > time.time() {
            # resting (daily limit, no credits, local model offline): go straight to the next model
            skipped += 1;
            out.last_error = name + " resting: " + str(rest[1]);
            if name.startswith("ollama") {
                out.local_model_error = f"skipped - resting {int(float(rest[0]) - time.time())}s ({rest[1]})";
            }
            continue;
        }
        out.models_attempted.append(name);
        for retry in range(2) {          # one retry on the same model, then fall back
            attempt += 1;
            try {
                raw: any = None;
                with _LOCK {             # f"cur" is shared: one model call at a time
                    cur = _model(name);
                    _CUR_NAME = name;
                    raw = fn(*args);
                }
                verdict = check(raw);
                status = str(verdict[0]);
                if status == "INVALID_OUTPUT" {
                    out.error_kind = "INVALID_OUTPUT";
                    out.last_error = "Output failed validation";
                    continue;
                }
                if status == "UNCERTAIN" and escalate_uncertain and i < len(models) - 1 {
                    out.last_error = "Uncertain; trying next model";
                    break;
                }
                out.status = status;
                out.value = verdict[1];
                out.model_used = name;
                out.attempt_number = attempt;
                out.fallback_count = i;
                return out;
            } except Exception as e {
                out.error_kind = _kind(e);
                out.last_error = str(e)[:300];
                if name.startswith("ollama") {
                    out.local_model_error = (type(e).__name__ + ": " + str(e))[:300];
                }
                if isinstance(e, NameError) {
                    # a hot reload of this module left the by-llm call pointing at stale types:
                    # a code problem, not a model failure - stop and let the caller retry later
                    out.error_kind = "CODE_RELOAD";
                    out.status = "RETRY_LATER";
                    return out;
                }
                rs = _rest_seconds(str(e), out.error_kind, name);
                if rs {
                    if name.startswith("ollama") and retry == 0 {
                        # a single flaky request through the quick tunnel is common and usually
                        # clears immediately; use the 2nd retry on the SAME model before writing
                        # the local model off and falling back to a cloud model for the next 2 min
                        continue;
                    }
                    # not a bad answer, the model is unavailable: rest it and switch model now
                    _REST[name] = [time.time() + float(rs[0]), str(rs[1])];
                    _save_rest();
                    print(f"[ai] {name} {rs[1]}; resting {int(rs[0])} s, switching model");
                    skipped += 1;
                    break;
                }
            }
        }
    }
    if models and skipped > 0 {
        # some model (usually the local Ollama behind a tunnel, or a cloud model out of credits)
        # was unavailable, so the task never got a fair try on every model: wait and retry.
        # Staff review is only for tasks every available model actually tried and failed.
        out.status = "RETRY_LATER";
        out.error_kind = "ALL_MODELS_RESTING";
        out.attempt_number = attempt;
        return out;
    }
    out.status = "STAFF_REVIEW";
    out.attempt_number = attempt;
    out.fallback_count = max(len(models) - 1, 0);
    return out;
}

# ---------- task 1: directory URL guesses ----------

obj DirectoryGuess {
    has department: str = "",
        url: str = "";
}
sem DirectoryGuess.department = "Academic unit the page belongs to, e.g. 'Computer Science'.";
sem DirectoryGuess.url = "Absolute URL on the university's own domain.";

def _guess_dirs(university: str, domain: str, fields: str) -> list[DirectoryGuess] by cur(max_tokens=ai_max_tokens());
sem _guess_dirs = "Propose up to 8 URLs of official FACULTY DIRECTORY pages (pages listing a department's professors) at this university. Cover every academic area: engineering, sciences, humanities, arts and social sciences. Only URLs on the given domain or its subdomains. Return an empty list if you do not know.";
sem _guess_dirs.university = "University name.";
sem _guess_dirs.domain = "The university's official web domain, e.g. umich.edu.";
sem _guess_dirs.fields = "Academic fields to prioritise.";

def _check_guesses(raw: any) -> list[any] {
    if not isinstance(raw, list) { return ["INVALID_OUTPUT", None]; }
    rows = [g for g in raw if isinstance(g, DirectoryGuess) and str(g.url).startswith("http")][:10];
    return ["VALID_RESULT" if rows else "VALID_EMPTY", rows];
}

def guess_directories(university: str, domain: str, fields: str) -> AiOutcome {
    return run_task(_guess_dirs, [university, domain, fields], _check_guesses);
}

# ---------- task 2: is this fetched page a faculty directory? ----------

enum PageKind { FACULTY_DIRECTORY, MIXED_DIRECTORY, NOT_FACULTY_DIRECTORY, UNCERTAIN }
sem PageKind.FACULTY_DIRECTORY = "The page mainly lists a unit's faculty/professors.";
sem PageKind.MIXED_DIRECTORY = "The page lists faculty together with staff or students.";
sem PageKind.NOT_FACULTY_DIRECTORY = "News, events, admissions, a single profile, or anything else.";

def _classify_page(page_text: str, url: str) -> PageKind by cur(max_tokens=ai_max_tokens());
sem _classify_page = "Classify this already-fetched university web page.";
sem _classify_page.page_text = "Text of the page (links shown as [text](url)).";

def _check_kind(raw: any) -> list[any] {
    if not isinstance(raw, PageKind) { return ["INVALID_OUTPUT", None]; }
    return ["UNCERTAIN" if raw == PageKind.UNCERTAIN else "VALID_RESULT", raw.name];
}

def classify_page(page_text: str, url: str) -> AiOutcome {
    return run_task(_classify_page, [page_text[:page_cap(14000)], url], _check_kind);
}

# ---------- task 3: faculty extraction from a hard page ----------

obj FacultyRow {
    has name: str = "",
        title: str = "";
}
sem FacultyRow.name = "Full name exactly as written on the page.";
sem FacultyRow.title = "Academic title as written (Professor, Associate Professor, Assistant Professor...).";

obj FacultyList {
    has uncertain: bool = False,
        faculty: list[FacultyRow] = [];
}
sem FacultyList.uncertain = "True if you cannot tell which people on the page are current faculty.";
sem FacultyList.faculty = "Every current professor-rank faculty member on the page. Exclude staff, students, emeriti, adjuncts, lecturers, visitors.";

def _extract_faculty(page_text: str, department: str) -> FacultyList by cur(max_tokens=ai_max_tokens());
sem _extract_faculty = "List the current professor-rank faculty that appear in this already-fetched faculty directory page. Only people present in the text.";
sem _extract_faculty.department = "Department hint for the page.";

def extract_faculty(page_text: str, department: str) -> AiOutcome {
    low = page_text.lower();
    def check(raw: any) -> list[any] {
        if not isinstance(raw, FacultyList) { return ["INVALID_OUTPUT", None]; }
        if raw.uncertain and len(raw.faculty) == 0 { return ["UNCERTAIN", []]; }
        # names must exist in the fetched text: the model cannot invent people
        rows = [r for r in raw.faculty if r.name.strip() and r.name.strip().lower() in low];
        return ["VALID_RESULT" if rows else "VALID_EMPTY", rows];
    }
    return run_task(_extract_faculty, [page_text[:page_cap(30000)], department], check, True);
}

# ---------- task 4: faculty profile details ----------

obj PublicationRef {
    has title: str = "",
        doi: str = "",
        year: int = 0;
}
obj ProfileInfo {
    has lab_url: str = "",
        personal_url: str = "",
        orcid: str = "",
        publications: list[PublicationRef] = [];
}
sem ProfileInfo.lab_url = "URL of the professor's lab or research group website if linked, else empty.";
sem ProfileInfo.personal_url = "URL of a personal homepage if linked, else empty.";
sem ProfileInfo.orcid = "ORCID iD (0000-0000-0000-0000) if shown, else empty.";
sem ProfileInfo.publications = "Up to 3 of the most recent publications listed on the page; empty if none. Never invent.";

def _profile(page_text: str, professor_name: str) -> ProfileInfo by cur(max_tokens=ai_max_tokens());
sem _profile = "Extract details from this already-fetched faculty profile page. Use only what appears in the text.";

def _check_profile(raw: any) -> list[any] {
    if not isinstance(raw, ProfileInfo) { return ["INVALID_OUTPUT", None]; }
    return ["VALID_RESULT", raw];
}

def extract_profile(page_text: str, professor_name: str) -> AiOutcome {
    return run_task(_profile, [page_text[:page_cap(14000)], professor_name], _check_profile);
}

# ---------- task 5: hiring signal on an already-fetched page ----------

enum HiringStatus { DIRECT_HIRING, INDIRECT_HIRING, GENERAL_RECRUITMENT, UNCERTAIN, NO_SIGNAL }
sem HiringStatus.DIRECT_HIRING = "This professor or lab explicitly says they are recruiting / have openings (e.g. 'We are recruiting two PhD students for Fall 2027').";
sem HiringStatus.INDIRECT_HIRING = "Invites prospective students/postdocs to contact the professor or apply to join the lab (e.g. 'Prospective PhD students interested in joining the lab should contact Professor Smith').";
sem HiringStatus.GENERAL_RECRUITMENT = "Department- or university-wide recruitment that does not name this professor's lab (e.g. 'Our department is accepting PhD applications').";
sem HiringStatus.UNCERTAIN = "Vague openness that is not explicit (e.g. 'We are always interested in hearing from talented students').";
sem HiringStatus.NO_SIGNAL = "No OPEN, forward-looking invitation to apply or join. These are all NO_SIGNAL: lists of current lab members; news that someone joined, was welcomed, hired, appointed or awarded a fellowship (e.g. 'We are delighted to welcome X as our Postdoctoral Fellow'); past positions; alumni; graduations; course or event announcements.";

obj HiringFinding {
    has status: HiringStatus = HiringStatus.NO_SIGNAL,
        quote: str = "",
        confidence: float = 0.0;
}
sem HiringFinding.quote = "The single sentence copied EXACTLY from the page text that supports the status. Empty for NO_SIGNAL.";
sem HiringFinding.confidence = "Your confidence from 0 to 1.";

def _hiring(page_text: str, professor_name: str) -> HiringFinding by cur(max_tokens=ai_max_tokens());
sem _hiring = "Decide whether this already-fetched page contains a hiring or recruitment statement by this professor or their lab, and copy the exact supporting sentence. Do not paraphrase. Do not return a URL.";

def _check_hiring(raw: any) -> list[any] {
    if not isinstance(raw, HiringFinding) { return ["INVALID_OUTPUT", None]; }
    if raw.status == HiringStatus.NO_SIGNAL { return ["VALID_EMPTY", raw]; }
    if not raw.quote.strip() { return ["INVALID_OUTPUT", None]; }    # claimed a signal but gave no quote
    if raw.status == HiringStatus.UNCERTAIN { return ["UNCERTAIN", raw]; }
    return ["VALID_RESULT", raw];
}

def extract_hiring(page_text: str, professor_name: str) -> AiOutcome {
    # UNCERTAIN is a valid hiring classification: not escalated.
    return run_task(_hiring, [page_text[:page_cap(20000)], professor_name], _check_hiring);
}

# Second opinion on one candidate sentence: is it really an open invitation to apply?
obj HiringVerdict {
    has is_open_recruitment: bool = False,
        reason: str = "";
}
sem HiringVerdict.is_open_recruitment = "True ONLY if the sentence invites people to apply for, contact about, or join an opening that exists now or in the future (PhD students, postdocs, research assistants, staff) in this professor's lab or group, or by this professor. Also True when it invites people interested in working with or joining the lab to contact the professor (indirect recruitment). False for announcements that someone joined, was welcomed, hired, appointed, promoted or received a fellowship or award; for descriptions of current or past members; for the professor's own appointment; and for anything in the past tense.";
sem HiringVerdict.reason = "One short sentence explaining the decision.";

def _verify_hiring(sentence: str, professor_name: str) -> HiringVerdict by cur(max_tokens=ai_max_tokens());
sem _verify_hiring = "Strictly judge whether this one sentence, copied from a web page, is a real recruitment signal for the named professor's lab. A news item welcoming or announcing a new member (even a new postdoc or fellow) is NOT recruitment. When in doubt, answer false.";

def _check_verdict(raw: any) -> list[any] {
    if not isinstance(raw, HiringVerdict) { return ["INVALID_OUTPUT", None]; }
    return ["VALID_RESULT", raw];
}

def verify_hiring(sentence: str, professor_name: str) -> AiOutcome {
    return run_task(_verify_hiring, [sentence[:1500], professor_name], _check_verdict);
}

# ---------- task 6: publications of a professor nobody else could identify ----------

obj KnownPaper {
    has title: str = "",
        year: int = 0,
        doi: str = "",
        confidence: float = 0.0;
}
sem KnownPaper.title = "Exact title of a paper, book, chapter or article you are sure THIS person wrote.";
sem KnownPaper.year = "Publication year, 0 if unknown.";
sem KnownPaper.doi = "DOI if you know it exactly, else empty. Never invent a DOI.";
sem KnownPaper.confidence = "Your confidence from 0 to 1 that this work is by THIS exact person at THIS university in THIS field.";

obj PublicationAnswer {
    has papers: list[KnownPaper] = [],
        reasoning: str = "";
}
sem PublicationAnswer.papers = "At most 5 works you are confident belong to this exact person. An EMPTY list is a correct, acceptable answer.";
sem PublicationAnswer.reasoning = "One short sentence on how you know (or why you do not).";

def _publications(professor_name: str, university: str, department: str, title: str, page_text: str) -> PublicationAnswer by cur(max_tokens=ai_max_tokens());
sem _publications = "/no_think ACCURACY FIRST. List publications of this specific professor ONLY if you are confident they were written by this exact person: same name, at this university, working in this department's field. Many people share a name - a work by a same-named person at another institution or in another field is WRONG and must be left out. It is completely fine, and often correct, that the professor has no publications you know of (many humanities and arts faculty publish books, exhibitions or performances, or nothing indexed); in that case return an empty list. Never guess, never invent titles or DOIs. Use the faculty page text if given.";
sem _publications.professor_name = "Full name as listed on the university's faculty directory.";
sem _publications.university = "The university the professor works at now.";
sem _publications.department = "The professor's department (their field).";
sem _publications.title = "Academic title, e.g. Assistant Professor.";
sem _publications.page_text = "Text of the professor's official faculty profile page, possibly empty.";

def _check_publications(raw: any) -> list[any] {
    if not isinstance(raw, PublicationAnswer) { return ["INVALID_OUTPUT", None]; }
    rows = [p for p in raw.papers if p.title.strip() and p.confidence >= 0.8][:8];
    return ["VALID_RESULT" if rows else "VALID_EMPTY", rows];
}

def find_publications(professor_name: str, university: str, department: str, title: str, page_text: str) -> AiOutcome {
    # VALID_EMPTY ("no papers") is a real answer and is not escalated to the next model.
    return run_task(_publications, [professor_name, university, department, title, page_text[:page_cap(10000)]], _check_publications);
}
