"""Run the pipeline for one professor from the profile page (staff only).
The logic lives in services/pipe.py and writes to MongoDB."""

import services.store as st;
import services.pipe as pipe;
import services.fetchers as fx;
import from services.access { require_staff, check_worker_token }

obj PipelineResult {
    has ok: bool = True,
        message: str = "";
}

def _run(professor_id: str) -> PipelineResult {
    p = st.get_professor(professor_id);
    if p is None { return PipelineResult(ok=False, message="Professor not found."); }
    inst = st.get_institution(str((p as dict).get("institution_id", "")));
    if inst is None { return PipelineResult(ok=False, message="Professor has no institution."); }
    try {
        st.update_professor(professor_id, {"pipeline_done": False});
        return PipelineResult(message=str(pipe.process_professor(p, inst)));
    } except fx.RateLimited as e {
        return PipelineResult(ok=False, message="OpenAlex rate limit: " + str(e));
    } except Exception as e {
        return PipelineResult(ok=False, message="Pipeline failed: " + str(e)[:200]);
    }
}

def:protect run_pipeline_for_professor(professor_id: str) -> PipelineResult {
    require_staff();
    return _run(professor_id);
}

def:pub internal_run_professor(worker_token: str = "", professor_id: str = "") -> PipelineResult {
    if not check_worker_token(worker_token) { return PipelineResult(ok=False, message="Forbidden"); }
    return _run(professor_id);
}
