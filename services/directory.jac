"""Public directory endpoints (no sign-in needed): anyone can search and open profiles.
Data lives in MongoDB (services/store.py)."""

import services.store as st;
import services.pipe as pipe;

obj ProfessorSummary {
    has id: str = "",
        name: str = "",
        title: str = "",
        department: str = "",
        university: str = "",
        subfields: list[str] = [],
        fields: list[str] = [],
        grant_count: int = 0,
        has_hiring: bool = False,
        match_status: str = "";
}

obj SearchResults {
    has items: list[ProfessorSummary] = [],
        total: int = 0,
        offset: int = 0;
}

obj FilterOptions {
    has universities: list[str] = [],
        departments: list[str] = [],
        subfields: list[str] = [],
        fields: list[str] = [];
}

obj AnchorView {
    has title: str = "",
        doi: str = "",
        year: int = 0,
        url: str = "",
        openalex_work_id: str = "",
        used_for_match: bool = False;
}

obj PaperView {
    has id: str = "",
        title: str = "",
        year: int = 0,
        publication_date: str = "",
        source_name: str = "",
        doi: str = "",
        url: str = "",
        citation_count: int = 0,
        subfields: list[str] = [];
}

obj GrantView {
    has id: str = "",
        title: str = "",
        funder_name: str = "",
        funder_award_id: str = "",
        role: str = "",
        amount: float = 0.0,
        currency: str = "",
        start_date: str = "",
        end_date: str = "",
        url: str = "",
        source: str = "",
        evidence: str = "";
}

obj ProfessorDetail {
    has id: str = "",
        name: str = "",
        title: str = "",
        department: str = "",
        university: str = "",
        university_state: str = "",
        faculty_url: str = "",
        lab_url: str = "",
        orcid: str = "",
        openalex_author_id: str = "",
        match_status: str = "",
        match_method: str = "",
        match_note: str = "",
        subfields: list[str] = [],
        anchors: list[AnchorView] = [],
        papers: list[PaperView] = [],
        grants: list[GrantView] = [],
        hiring_quote: str = "",
        hiring_source_url: str = "",
        hiring_source_title: str = "",
        hiring_last_checked: str = "",
        last_openalex_update: str = "",
        affiliation_status: str = "",
        affiliation_note: str = "",
        current_page_url: str = "";
}

def _flag(v: any) -> bool {
    # REST callers sometimes send "False"/"True" strings.
    return v is True or str(v).lower() == "true";
}

def _summary(r: dict) -> ProfessorSummary {
    return ProfessorSummary(
        id=str(r.get("id", "")), name=str(r.get("name", "")), title=str(r.get("title", "")),
        department=str(r.get("department", "")), university=str(r.get("university", "")),
        subfields=list(r.get("subfields") or []), fields=list(r.get("fields") or []),
        grant_count=int(r.get("grant_count") or 0), has_hiring=bool(r.get("has_hiring")),
        match_status=str(r.get("match_status", ""))
    );
}

def:pub search_professors_page(
    query: str = "",
    university: str = "",
    department: str = "",
    subfield: str = "",
    has_grant: bool = False,
    has_hiring: bool = False,
    offset: int = 0,
    limit: int = 50
) -> SearchResults {
    # One page of results: the Search page loads 50, then 20 more per "Load more".
    pipe.note_search();   # the background pipeline pauses while people are searching
    res = st.search(query, university, department, subfield, _flag(has_grant), _flag(has_hiring), offset, limit) as dict;
    return SearchResults(
        items=[_summary(r) for r in (res["items"] as list[dict])], total=int(res["total"]), offset=int(res["offset"])
    );
}

def:pub search_professors(
    query: str = "",
    university: str = "",
    department: str = "",
    subfield: str = "",
    has_grant: bool = False,
    has_hiring: bool = False
) -> list[ProfessorSummary] {
    # Kept for API compatibility: first 200 matches.
    return search_professors_page(query, university, department, subfield, has_grant, has_hiring, 0, 200).items;
}

def:pub get_filter_options(university: str = "", department: str = "") -> FilterOptions {
    o = st.filter_options(university, department) as dict;
    return FilterOptions(
        universities=o["universities"], departments=o["departments"], subfields=o["subfields"], fields=o["fields"]
    );
}

def:pub get_professor_profile(professor_id: str) -> ProfessorDetail | None {
    found = st.get_professor(professor_id);
    if found is None {
        return None;
    }
    p: dict = found as dict;
    inst_raw = st.get_institution(str(p.get("institution_id", "")));
    inst: dict = (inst_raw as dict) if inst_raw is not None else {};
    papers: list[PaperView] = [];
    for x in (st.papers_by_ids(p.get("paper_ids") or []) as list[dict]) {
        papers.append(PaperView(
            id=str(x.get("id", "")), title=str(x.get("title", "")), year=int(x.get("publication_year") or 0),
            publication_date=str(x.get("publication_date", "")), source_name=str(x.get("source_name", "")),
            doi=str(x.get("doi", "")), url=str(x.get("paper_url", "")), citation_count=int(x.get("citation_count") or 0),
            subfields=[str(x.get("subfield"))] if x.get("subfield") else []
        ));
    }
    papers.sort(key=lambda (v: PaperView) -> str { return v.publication_date; }, reverse=True);
    roles: dict = {};
    for g in (p.get("grants") or []) { roles[str(g.get("id", ""))] = str(g.get("role", "")); }
    grants: list[GrantView] = [];
    for g2 in (st.grants_by_ids(list(roles.keys())) as list[dict]) {
        grants.append(GrantView(
            id=str(g2.get("id", "")), title=str(g2.get("title", "")), funder_name=str(g2.get("funder_name", "")),
            funder_award_id=str(g2.get("funder_award_id", "")), role=roles.get(str(g2.get("id", "")), ""),
            amount=float(g2.get("amount") or 0.0), currency=str(g2.get("currency", "")),
            start_date=str(g2.get("start_date", "")), end_date=str(g2.get("end_date", "")),
            url=str(g2.get("landing_page_url", "")),
            source=str(g2.get("source") or ("OPENALEX" if not str(g2.get("id", "")).startswith(("FG", "NSF-", "NIH-")) else "")),
            evidence=str(g2.get("evidence", "") or "")
        ));
    }
    # newest award first; undated ones last
    grants.sort(key=lambda (v: GrantView) -> str { return v.start_date or "0000"; }, reverse=True);
    anchors = [
        AnchorView(
            title=str(a.get("title", "")), doi=str(a.get("doi", "")), year=int(a.get("publication_year") or 0),
            url=str(a.get("url", "")), openalex_work_id=str(a.get("openalex_work_id", "")),
            used_for_match=bool(a.get("used_for_match"))
        )
        for a in (p.get("anchors") or [])
    ];
    h: dict = p.get("hiring") or {};
    return ProfessorDetail(
        id=str(p["id"]), name=str(p.get("name", "")), title=str(p.get("title", "")),
        department=str(p.get("department", "")), university=str(p.get("university", "")),
        university_state=str(inst.get("state", "")),
        faculty_url=str(p.get("faculty_url", "")), lab_url=str(p.get("lab_url", "")), orcid=str(p.get("orcid", "")),
        openalex_author_id=str(p.get("openalex_author_id", "")), match_status=str(p.get("match_status", "")),
        match_method=str(p.get("match_method", "")), match_note=str(p.get("match_note", "")),
        subfields=list(p.get("subfields") or []), anchors=anchors, papers=papers, grants=grants,
        hiring_quote=str(h.get("quote", "") or ""), hiring_source_url=str(h.get("source_url", "") or ""),
        hiring_source_title=str(h.get("source_title", "") or ""), hiring_last_checked=str(h.get("last_checked", "") or ""),
        last_openalex_update=str(p.get("last_openalex_update", "")),
        affiliation_status=str(p.get("affiliation_status", "") or ""),
        affiliation_note=str(p.get("affiliation_note", "") or ""),
        current_page_url=str(p.get("current_page_url", "") or "")
    );
}
