import from services.pipeline { run_pipeline_for_professor, PipelineResult }
import from services.queue {
    load_priority_universities, get_pipeline_status, process_next_step, get_worker_state,
    set_worker, reset_institution, get_config_status,
    InstitutionStatus, StepResult, WorkerState, ConfigStatus
}
import from services.directory {
    search_professors, get_filter_options, get_professor_profile,
    ProfessorSummary, FilterOptions, ProfessorDetail, PaperView, GrantView, AnchorView
}

import ".styles.global.css";
import from "@jac/runtime" { Router, Routes, Route, Link }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Search01Icon }
import from .components.SearchPage { SearchPage }
import from .components.ProfilePage { ProfilePage }
import from .components.PipelineAdmin { PipelineAdmin }

def:pub TopNav() -> JsxElement {
    return <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 pl-6 pr-6">
            <Link to="/" className="flex items-center gap-2.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
                    <HugeiconsIcon icon={Search01Icon} strokeWidth={2} className="size-4" />
                </div>
                <span className="text-base font-semibold tracking-tight">{"Professor Atlas"}</span>
            </Link>
            <nav className="ml-auto flex items-center gap-1 text-sm">
                <Link to="/" className="rounded-md pt-1.5 pb-1.5 pl-3 pr-3 font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">{"Search"}</Link>
                <Link to="/pipeline" className="rounded-md pt-1.5 pb-1.5 pl-3 pr-3 font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">{"Pipeline"}</Link>
            </nav>
        </div>
    </header>;
}

def:pub app() -> JsxElement {
    return <Router>
        <div className="flex min-h-svh flex-col bg-muted/40">
            <TopNav />
            <main className="flex-1">
                <Routes>
                    <Route path="/" element={<SearchPage />} />
                    <Route path="/professors/:id" element={<ProfilePage />} />
                    <Route path="/pipeline" element={<PipelineAdmin />} />
                </Routes>
            </main>
        </div>
    </Router>;
}
