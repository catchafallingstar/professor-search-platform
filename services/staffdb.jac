"""Staff-only read views of the database (server-enforced with require_staff)."""

import from services.models {
    Institution, Professor, HasInstitution, HasProfessor, get_data_root, normalize_name
}
import from services.access { require_staff }

obj DbOverview {
    has institutions: int = 0,
        professors: int = 0,
        matched: int = 0,
        unresolved: int = 0,
        pending: int = 0,
        papers: int = 0,
        grants: int = 0,
        hiring: int = 0;
}

obj DbInstitution {
    has id: str = "",
        name: str = "",
        state: str = "",
        tier: str = "",
        pipeline_state: str = "",
        openalex_id: str = "",
        professors: int = 0,
        matched: int = 0,
        papers: int = 0,
        grants: int = 0;
}

obj DbProfessor {
    has id: str = "",
        name: str = "",
        title: str = "",
        department: str = "",
        university: str = "",
        match_status: str = "",
        openalex_author_id: str = "",
        subfields: int = 0,
        grants: int = 0,
        has_hiring: bool = False;
}

obj DbProfessorPage {
    has total: int = 0,
        rows: list[DbProfessor] = [];
}

def:priv staff_overview() -> DbOverview {
    require_staff();
    o = DbOverview();
    for inst in [get_data_root() ->:HasInstitution:->] {
        o.institutions += 1;
        o.professors += inst.n_professors;
        o.matched += inst.n_matched;
        o.unresolved += inst.n_unresolved;
        o.papers += inst.n_papers;
        o.grants += inst.n_grants;
        o.hiring += inst.n_hiring;
    }
    o.pending = max(o.professors - o.matched - o.unresolved, 0);
    return o;
}

def:priv staff_institutions() -> list[DbInstitution] {
    require_staff();
    rows = [
        DbInstitution(
            id=jid(i), name=i.name, state=i.state, tier=i.priority_reason, pipeline_state=i.pipeline_state,
            openalex_id=i.openalex_institution_id, professors=i.n_professors, matched=i.n_matched,
            papers=i.n_papers, grants=i.n_grants
        )
        for i in [get_data_root() ->:HasInstitution:->]
    ];
    rows.sort(key=lambda (r: DbInstitution) -> str { return r.name; });
    return rows;
}

def:priv staff_professors(query: str = "", match_status: str = "", offset: int = 0, limit: int = 50) -> DbProfessorPage {
    require_staff();
    q = normalize_name(query);
    matches: list[DbProfessor] = [];
    for inst in [get_data_root() ->:HasInstitution:->] {
        for p in [inst ->:HasProfessor:->] {
            if q and q not in normalize_name(p.name) and q not in p.department.lower() and q not in inst.name.lower() {
                continue;
            }
            if match_status and p.match_status != match_status {
                continue;
            }
            matches.append(DbProfessor(
                id=jid(p), name=p.name, title=p.title, department=p.department, university=inst.name,
                match_status=p.match_status, openalex_author_id=p.openalex_author_id,
                subfields=len(p.cached_subfields), grants=p.cached_grant_count, has_hiring=p.cached_has_hiring
            ));
        }
    }
    matches.sort(key=lambda (r: DbProfessor) -> str { return r.name; });
    lim = min(max(limit, 1), 200);
    start = max(offset, 0);
    return DbProfessorPage(total=len(matches), rows=matches[start:start + lim]);
}
