"""Read endpoints: professor search, filter options, and full profile."""

import services.fetchers as fx;
import from services.models {
    DataRoot, Institution, Professor, FacultyPublicationAnchor, Paper, Subfield,
    Grant, HiringSignal, HasInstitution, HasProfessor, HasAnchor, Authored,
    HasSubfield, HasGrant, HasHiringSignal, get_data_root, normalize_name
}

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
        url: str = "";
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
        last_openalex_update: str = "";
}

def _paper_subfields(paper: Paper) -> list[str] {
    return [s.name for s in [paper ->:HasSubfield:->]];
}

def _recent_subfields(p: Professor) -> list[str] {
    out: list[str] = [];
    for paper in [p ->:Authored:->] {
        for s in _paper_subfields(paper) {
            if s not in out {
                out.append(s);
            }
        }
    }
    out.sort();
    return out;
}

def _hiring_quote(p: Professor) -> str {
    for s in [p ->:HasHiringSignal:->] {
        if s.quote {
            return s.quote;
        }
    }
    return "";
}

def:pub search_professors(
    query: str = "",
    university: str = "",
    department: str = "",
    subfield: str = "",
    has_grant: bool = False,
    has_hiring: bool = False
) -> list[ProfessorSummary] {
    # Free-text search across name, university, department, research field and subfield.
    # Every word must appear somewhere (case/accent-insensitive), e.g. "michigan robotics".
    # Defaults can arrive as the strings "False"/"True" from REST callers; only real truthy values filter.
    has_grant = has_grant is True or str(has_grant).lower() == "true";
    has_hiring = has_hiring is True or str(has_hiring).lower() == "true";
    data = get_data_root();
    words = [w for w in normalize_name(query).split() if w];
    results: list[ProfessorSummary] = [];
    for inst in [data ->:HasInstitution:->] {
        if university and inst.name != university {
            continue;
        }
        inst_text = normalize_name(inst.name + " " + inst.city + " " + inst.state);
        for p in [inst ->:HasProfessor:->] {
            if len(words) > 0 {
                hay = p.search_text or normalize_name(" | ".join([p.name, p.title, p.department] + p.cached_fields + p.cached_subfields));
                hay = hay + " | " + inst_text;
                if not all([w in hay for w in words]) {
                    continue;
                }
            }
            if department and p.department != department {
                continue;
            }
            subs = p.cached_subfields;
            if subfield and subfield not in subs and subfield not in p.cached_fields {
                continue;
            }
            grants = p.cached_grant_count;
            if has_grant and grants == 0 {
                continue;
            }
            hiring = p.cached_has_hiring;
            if has_hiring and not hiring {
                continue;
            }
            results.append(
                ProfessorSummary(
                    id=jid(p), name=p.name, title=p.title, department=p.department,
                    university=inst.name, subfields=subs, fields=p.cached_fields, grant_count=grants,
                    has_hiring=hiring, match_status=p.match_status
                )
            );
        }
    }
    results.sort(key=lambda (r: ProfessorSummary) -> str { return r.name; });
    return results;
}

# In-memory search index: walking ~2000 professor nodes on every keystroke took 10-100s
# while the pipeline was busy. Rebuilt at most once a minute; filtering is then pure Python.
glob _SEARCH_CACHE: dict = {"t": 0.0, "rows": []};

def reset_search_cache() -> None {
    # Called after bulk imports so new professors appear in search immediately.
    _SEARCH_CACHE["t"] = 0.0;
}

def _search_rows() -> list {
    import time;
    now = time.time();
    if now - float(_SEARCH_CACHE["t"]) < 600 and len(_SEARCH_CACHE["rows"]) > 0 {
        return _SEARCH_CACHE["rows"];
    }
    rows: list = [];
    for inst in [get_data_root() ->:HasInstitution:->] {
        inst_text = normalize_name(inst.name + " " + inst.city + " " + inst.state);
        for p in [inst ->:HasProfessor:->] {
            hay = p.search_text or normalize_name(" | ".join([p.name, p.title, p.department] + p.cached_fields + p.cached_subfields));
            rows.append({
                "hay": hay + " | " + inst_text,
                "summary": ProfessorSummary(
                    id=jid(p), name=p.name, title=p.title, department=p.department,
                    university=inst.name, subfields=p.cached_subfields, fields=p.cached_fields,
                    grant_count=p.cached_grant_count, has_hiring=p.cached_has_hiring,
                    match_status=p.match_status
                )
            });
        }
    }
    rows.sort(key=lambda (r: dict) -> str { return r["summary"].name; });
    _SEARCH_CACHE["rows"] = rows;
    _SEARCH_CACHE["t"] = now;
    return rows;
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
    # One page of results (the Search page loads 50, then 20 more per "Load more").
    fx.note_search();   # background pipeline pauses while people are searching
    has_grant = has_grant is True or str(has_grant).lower() == "true";
    has_hiring = has_hiring is True or str(has_hiring).lower() == "true";
    words = [w for w in normalize_name(query).split() if w];
    everything: list[ProfessorSummary] = [];
    for r in _search_rows() {
        s: ProfessorSummary = r["summary"];
        if university and s.university != university { continue; }
        if department and s.department != department { continue; }
        if subfield and subfield not in s.subfields and subfield not in s.fields { continue; }
        if has_grant and s.grant_count == 0 { continue; }
        if has_hiring and not s.has_hiring { continue; }
        if len(words) > 0 and not all([w in r["hay"] for w in words]) { continue; }
        everything.append(s);
    }
    start = max(0, int(offset));
    size = max(1, min(int(limit), 200));
    return SearchResults(items=everything[start:start + size], total=len(everything), offset=start);
}

def:pub get_filter_options() -> FilterOptions {
    data = get_data_root();
    opts = FilterOptions();
    for inst in [data ->:HasInstitution:->] {
        if inst.name not in opts.universities {
            opts.universities.append(inst.name);
        }
        for p in [inst ->:HasProfessor:->] {
            if p.department and p.department not in opts.departments {
                opts.departments.append(p.department);
            }
            for s in p.cached_subfields {
                if s not in opts.subfields {
                    opts.subfields.append(s);
                }
            }
            for f in p.cached_fields {
                if f not in opts.fields {
                    opts.fields.append(f);
                }
            }
        }
    }
    opts.universities.sort();
    opts.departments.sort();
    opts.subfields.sort();
    opts.fields.sort();
    return opts;
}

def:pub get_professor_profile(professor_id: str) -> ProfessorDetail | None {
    target = jobj(professor_id);
    if not isinstance(target, Professor) {
        return None;
    }
    p = target as Professor;
    insts = [p <-:HasProfessor:<-];
    uni = insts[0].name if len(insts) > 0 else "";
    ustate = insts[0].state if len(insts) > 0 else "";
    anchors = [
        AnchorView(
            title=a.title, doi=a.doi, year=a.publication_year, url=a.url,
            openalex_work_id=a.openalex_work_id, used_for_match=a.used_for_match
        )
        for a in [p ->:HasAnchor:->]
    ];
    papers = [
        PaperView(
            id=jid(x), title=x.title, year=x.publication_year, publication_date=x.publication_date,
            source_name=x.source_name, doi=x.doi, url=x.paper_url, citation_count=x.citation_count,
            subfields=_paper_subfields(x)
        )
        for x in [p ->:Authored:->]
    ];
    papers.sort(key=lambda (v: PaperView) -> str { return v.publication_date; }, reverse=True);
    grants: list[GrantView] = [];
    for g2 in [p ->:HasGrant:->] {
        role = "";
        for e in [edge p ->:HasGrant:-> g2] {
            role = e.role;
        }
        grants.append(
            GrantView(
                id=jid(g2), title=g2.title, funder_name=g2.funder_name,
                funder_award_id=g2.funder_award_id, role=role, amount=g2.amount,
                currency=g2.currency, start_date=g2.start_date, end_date=g2.end_date,
                url=g2.landing_page_url
            )
        );
    }
    sigs = [p ->:HasHiringSignal:->];
    sig: HiringSignal | None = sigs[0] if len(sigs) > 0 else None;
    return ProfessorDetail(
        id=jid(p), name=p.name, title=p.title, department=p.department,
        university=uni, university_state=ustate, faculty_url=p.faculty_url,
        lab_url=p.lab_url, orcid=p.orcid, openalex_author_id=p.openalex_author_id,
        match_status=p.match_status, match_method=p.match_method, match_note=p.match_note,
        subfields=_recent_subfields(p), anchors=anchors, papers=papers, grants=grants,
        hiring_quote=sig.quote if sig else "",
        hiring_source_url=sig.source_url if sig else "",
        hiring_source_title=sig.source_title if sig else "",
        hiring_last_checked=sig.last_checked if sig else "",
        last_openalex_update=p.last_openalex_update
    );
}
