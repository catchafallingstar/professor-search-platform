import from services.pipeline { run_pipeline_for_professor, internal_run_professor, PipelineResult }
import from services.queue {
    load_priority_universities, get_pipeline_status, process_next_step, run_step_as_staff, get_worker_state,
    internal_action, download_snapshot, upload_snapshot, snapshot_info,
    set_worker, reset_institution, get_config_status, add_openalex_key, retry_unreadable, recount_all,
    list_openalex_keys, remove_openalex_key, reset_openalex_key, KeyStatus,
    InstitutionStatus, StepResult, WorkerState, ConfigStatus
}
import from services.directory {
    search_professors, search_professors_page, get_filter_options, get_professor_profile,
    ProfessorSummary, SearchResults, FilterOptions, ProfessorDetail, PaperView, GrantView, AnchorView
}
import from services.access { whoami, promote_staff, revoke_staff, list_staff, reset_staff_registry, WhoAmI, AccessResult }
import from services.history {
    record_search, record_view, get_search_history, get_view_history,
    clear_search_history, clear_view_history, SearchHistoryItem, ViewHistoryItem
}
import from services.staffdb {
    staff_overview, staff_institutions, staff_professors,
    DbOverview, DbInstitution, DbProfessor, DbProfessorPage
}

import ".styles.global.css";
import from "@jac/runtime" { Router, Routes, Route, Link, Navigate, useNavigate, useLocation }
import from "@hugeicons/react" { HugeiconsIcon }
import from "@hugeicons/core-free-icons" { Search01Icon }
import from .components.SearchPage { SearchPage }
import from .components.ProfilePage { ProfilePage }
import from .components.PipelineAdmin { PipelineAdmin }
import from .components.AuthPage { AuthPage }
import from .components.AccountPage { AccountPage }
import from .components.StaffDatabase { StaffDatabase }
import from .components.RouteGuards { RequireUser, RequireStaff }
import from .components.AuthContext { useAuth, signOut }

def:pub NavItem(to: str, label: str) -> JsxElement {
    path = useLocation().pathname;
    active = path == to or (to != "/" and path.startswith(to));
    return <Link to={to} className={"rounded-md pt-1.5 pb-1.5 pl-3 pr-3 font-medium transition-colors hover:bg-muted hover:text-foreground " + ("bg-muted text-foreground" if active else "text-muted-foreground")}>{label}</Link>;
}

def:pub TopNav() -> JsxElement {
    auth = useAuth();
    nav = useNavigate();
    me: any = auth["me"];
    return <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-6xl items-center gap-4 pl-6 pr-6">
            <Link to="/" className="flex items-center gap-2.5">
                <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
                    <HugeiconsIcon icon={Search01Icon} strokeWidth={2} className="size-4" />
                </div>
                <span className="hidden text-base font-semibold tracking-tight sm:inline">{"Professor Atlas"}</span>
            </Link>
            <nav className="ml-auto flex items-center gap-1 text-sm">
                <NavItem to="/" label="Search" />
                {me and <NavItem to="/account" label="My account" />}
                {me and me.is_staff and <NavItem to="/staff" label="Staff" />}
                {me and <button type="button" onClick={lambda { signOut(); nav("/"); }}
                    className="rounded-md pt-1.5 pb-1.5 pl-3 pr-3 font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">{"Sign out"}</button>}
                {auth["ready"] and not me and <Link to="/login" className="ml-1 rounded-md bg-primary pt-1.5 pb-1.5 pl-3 pr-3 font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90">{"Sign in"}</Link>}
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
                    <Route path="/login" element={<AuthPage />} />
                    <Route path="/account" element={<RequireUser><AccountPage /></RequireUser>} />
                    <Route path="/staff" element={<RequireStaff><PipelineAdmin /></RequireStaff>} />
                    <Route path="/staff/database" element={<RequireStaff><StaffDatabase /></RequireStaff>} />
                    <Route path="/pipeline" element={<Navigate to="/staff" replace={True} />} />
                    <Route path="*" element={<Navigate to="/" replace={True} />} />
                </Routes>
            </main>
        </div>
    </Router>;
}
