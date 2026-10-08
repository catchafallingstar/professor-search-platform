"""Staff database views, read from MongoDB (services/store.py)."""

import services.store as st;
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

def:protect staff_overview() -> DbOverview {
    require_staff();
    o = st.overview() as dict;
    return DbOverview(
        institutions=int(o["institutions"]), professors=int(o["professors"]), matched=int(o["matched"]),
        unresolved=int(o["unresolved"]), pending=max(int(o["professors"]) - int(o["matched"]) - int(o["unresolved"]), 0),
        papers=int(o["papers"]), grants=int(o["grants"]), hiring=int(o["hiring"])
    );
}

def:protect staff_institutions() -> list[DbInstitution] {
    require_staff();
    rows = [
        DbInstitution(
            id=str(i["id"]), name=str(i.get("name", "")), state=str(i.get("state", "")),
            tier=str(i.get("priority_reason", "")), pipeline_state=str(i.get("pipeline_state", "")),
            openalex_id=str(i.get("openalex_institution_id", "")), professors=int(i.get("n_professors") or 0),
            matched=int(i.get("n_matched") or 0), papers=int(i.get("n_papers") or 0), grants=int(i.get("n_grants") or 0)
        )
        for i in (st.list_institutions({}, False) as list[dict])
    ];
    rows.sort(key=lambda (r: DbInstitution) -> str { return r.name; });
    return rows;
}

def:protect staff_professors(query: str = "", match_status: str = "", offset: int = 0, limit: int = 50) -> DbProfessorPage {
    require_staff();
    res = st.search(query, "", "", "", False, False, 0, 100000) as dict;
    rows: list[DbProfessor] = [];
    for r in (res["items"] as list[dict]) {
        if match_status and str(r.get("match_status", "")) != match_status { continue; }
        rows.append(DbProfessor(
            id=str(r["id"]), name=str(r.get("name", "")), title=str(r.get("title", "")),
            department=str(r.get("department", "")), university=str(r.get("university", "")),
            match_status=str(r.get("match_status", "")), openalex_author_id="",
            subfields=len(r.get("subfields") or []), grants=int(r.get("grant_count") or 0),
            has_hiring=bool(r.get("has_hiring"))
        ));
    }
    lim = min(max(limit, 1), 200);
    start = max(offset, 0);
    return DbProfessorPage(total=len(rows), rows=rows[start:start + lim]);
}
