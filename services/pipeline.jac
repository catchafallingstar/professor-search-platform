"""Per-professor pipeline: OpenAlex identity match -> recent papers + subfields -> grants -> hiring.

Graph work is Jac; HTTP is Python (services/fetchers.py) behind services/openalex.jac.
"""

import from services.models {
    DataRoot, Institution, Professor, FacultyPublicationAnchor, Paper, Subfield,
    Grant, HiringSignal, HasProfessor, HasAnchor, Authored, HasSubfield, HasGrant,
    HasHiringSignal, HasSubfieldIndex, HasPaperIndex, HasGrantIndex,
    get_data_root, now_iso, normalize_name, find_professor
}
import from services.openalex {
    fetch_work_by_doi, search_work_by_title, search_authors, fetch_author,
    fetch_author_works, fetch_award, search_institution, fetch_institution_awards,
    short_id, clean_doi
}
import from services.hiring { HiringResearch, research_hiring }
import from services.access { require_staff, check_worker_token }
import os;
import services.fetchers as fx;
import from services.crawler { fetch_page, find_hiring_quote, quote_on_page, llm_ready }

obj PipelineResult {
    has ok: bool = True,
        message: str = "";
}

def _s(d: any, key: str) -> str {
    return str(d.get(key, "") or "") if isinstance(d, dict) else "";
}

# ---------- identity ----------

def names_match(prof_name: str, candidate: str) -> bool {
    a = normalize_name(prof_name).split();
    b = normalize_name(candidate).split();
    if len(a) == 0 or len(b) == 0 { return False; }
    if a[-1] == b[-1] {
        if a[0][0] == b[0][0] { return True; }
        # nickname / middle-name form: "Chad Jenkins" vs "Odest Chadwicke Jenkins"
        for g in b[:-1] {
            if len(a[0]) >= 3 and (g.startswith(a[0]) or a[0].startswith(g)) and len(g) >= 3 { return True; }
        }
        return False;
    }
    return a[0] == b[-1] and a[-1] == b[0];   # family-name-first ordering
}

def institution_of(p: Professor) -> Institution | None {
    found = [p <-:HasProfessor:<-];
    return found[0] if len(found) > 0 else None;
}

def resolve_institution(inst: Institution) -> None {
    if inst.openalex_institution_id { return; }
    data = search_institution(inst.name, inst.ror_id);
    if data is None { return; }
    inst.openalex_institution_id = short_id(_s(data, "id"));
    if not inst.ror_id { inst.ror_id = short_id(_s(data, "ror")); }
    inst.updated_at = now_iso();
}

def _authorship_at(au: dict, inst_id: str) -> bool {
    for i in (au.get("institutions", []) or []) {
        if inst_id and short_id(_s(i, "id")) == inst_id { return True; }
    }
    return False;
}

def _author_at(author_id: str, inst_id: str) -> bool {
    if not inst_id { return False; }
    a = fetch_author(author_id);
    if a is None { return False; }
    for i in (a.get("last_known_institutions", []) or []) {
        if short_id(_s(i, "id")) == inst_id { return True; }
    }
    for af in (a.get("affiliations", []) or []) {
        if short_id(_s(af.get("institution", {}) or {}, "id")) == inst_id { return True; }
    }
    return False;
}

def _affiliated(author: dict, inst_id: str) -> bool {
    if not inst_id { return False; }
    for i in (author.get("last_known_institutions", []) or []) {
        if short_id(_s(i, "id")) == inst_id { return True; }
    }
    for af in (author.get("affiliations", []) or []) {
        if short_id(_s(af.get("institution", {}) or {}, "id")) == inst_id { return True; }
    }
    return False;
}

def match_professor(p: Professor, inst: Institution) -> None {
    if p.openalex_author_id { return; }
    inst_id = inst.openalex_institution_id;
    # A/B: faculty-page publications (DOI first, else exact title), at most 3
    for anc in [p ->:HasAnchor:->][:3] {
        works: list[dict] = [];
        method = "DOI";
        if anc.doi {
            w = fetch_work_by_doi(anc.doi);
            if w is not None { works = [w]; }
        }
        if len(works) == 0 and anc.title {
            method = "TITLE";
            target = normalize_name(anc.title);
            for c in search_work_by_title(anc.title) {
                if normalize_name(_s(c, "title")) == target { works = [c]; break; }
            }
        }
        for w in works {
            for au in (w.get("authorships", []) or []) {
                author = au.get("author", {}) or {};
                aid = short_id(_s(author, "id"));
                if not aid or not names_match(p.name, _s(author, "display_name")) { continue; }
                verified = _authorship_at(au, inst_id) or _author_at(aid, inst_id);
                p.openalex_author_id = aid;
                if not p.orcid { p.orcid = short_id(_s(author, "orcid")); }
                anc.openalex_work_id = short_id(_s(w, "id"));
                anc.used_for_match = True;
                p.match_status = "MATCHED";
                p.match_method = "PAPER_" + method;
                p.match_note = "Matched via faculty-page publication \"" + anc.title + "\"; " + (
                    "institution verified." if verified else "institution not verified (may have moved)."
                );
                return;
            }
        }
    }
    # C: name + institution, accept only one clear author
    if not inst_id {
        p.match_status = "UNRESOLVED";
        p.match_note = "Institution not resolved in OpenAlex.";
        return;
    }
    cands = [a for a in search_authors(p.name, inst_id) if names_match(p.name, _s(a, "display_name"))];
    note = "Matched on name + institution (one clear OpenAlex author).";
    if len(cands) == 0 {
        # Last-known institution may lag; accept an author whose affiliation history includes this university.
        cands = [
            a for a in search_authors(p.name, "")
            if names_match(p.name, _s(a, "display_name")) and _affiliated(a, inst_id)
        ];
        note = "Matched on name + institution in OpenAlex affiliation history.";
    }
    if p.orcid {
        by_orcid = [a for a in cands if short_id(_s(a, "orcid")) == p.orcid];
        if len(by_orcid) == 1 { cands = by_orcid; }
    }
    if len(cands) > 1 {
        # OpenAlex often splits one person into a main profile plus tiny fragments.
        cands.sort(key=lambda (a: dict) -> int { return int(a.get("works_count", 0) or 0); }, reverse=True);
        top = int(cands[0].get("works_count", 0) or 0);
        rest = sum([int(a.get("works_count", 0) or 0) for a in cands[1:]]);
        if top >= 20 and top >= 5 * max(rest, 1) {
            cands = [cands[0]];
            note = "Matched on name + institution (dominant OpenAlex profile; smaller duplicates ignored).";
        }
    }
    if len(cands) == 1 {
        p.openalex_author_id = short_id(_s(cands[0], "id"));
        if not p.orcid { p.orcid = short_id(_s(cands[0], "orcid")); }
        p.match_status = "MATCHED";
        p.match_method = "NAME_INSTITUTION";
        p.match_note = note;
    } else {
        p.match_status = "UNRESOLVED";
        p.match_method = "";
        p.match_note = "No OpenAlex author for name + institution." if len(cands) == 0 else f"Ambiguous: {len(cands)} OpenAlex authors with this name at the institution.";
    }
}

# ---------- papers ----------

def upsert_subfield(data: DataRoot, sid: str, name: str, field_name: str = "") -> Subfield {
    for s in [data ->:HasSubfieldIndex:->] {
        if (sid and s.openalex_subfield_id == sid) or (not sid and s.name == name) {
            if field_name and not s.field_name { s.field_name = field_name; }
            return s;
        }
    }
    s = Subfield(openalex_subfield_id=sid, name=name, field_name=field_name);
    data +>:HasSubfieldIndex():+> s;
    return s;
}

def _upsert_paper(data: DataRoot, w: dict) -> Paper {
    wid = short_id(_s(w, "id"));
    paper: Paper | None = None;
    for x in [data ->:HasPaperIndex:->] {
        if x.openalex_work_id == wid { paper = x; break; }
    }
    if paper is None {
        paper = Paper(openalex_work_id=wid, created_at=now_iso());
        data +>:HasPaperIndex():+> paper;
    }
    loc = w.get("primary_location", {}) or {};
    doi = _s(w, "doi");
    paper.doi = clean_doi(doi) if doi else "";
    paper.title = _s(w, "title") or _s(w, "display_name");
    paper.publication_date = _s(w, "publication_date");
    paper.publication_year = int(w.get("publication_year", 0) or 0);
    paper.source_name = _s(loc.get("source", {}) or {}, "display_name");
    paper.paper_url = doi or _s(loc, "landing_page_url") or _s(w, "id");
    paper.citation_count = int(w.get("cited_by_count", 0) or 0);
    paper.updated_at = now_iso();
    topic = w.get("primary_topic", {}) or {};
    sub = topic.get("subfield", {}) or {};
    fld = topic.get("field", {}) or {};
    sname = _s(sub, "display_name");
    fname = _s(fld, "display_name");
    existing = [paper ->:HasSubfield:->];
    if sname and len(existing) == 0 {
        paper +>:HasSubfield():+> upsert_subfield(data, short_id(_s(sub, "id")), sname, fname);
    } elif fname {
        for s in existing {
            if not s.field_name { s.field_name = fname; }
        }
    }
    return paper;
}

def ingest_works(p: Professor) -> list[str] {
    import from datetime { datetime }
    data = get_data_root();
    have = [x.openalex_work_id for x in [p ->:Authored:->]];
    award_ids: list[str] = [];
    for w in fetch_author_works(p.openalex_author_id, datetime.now().year - 4) {
        paper = _upsert_paper(data, w);
        if paper.openalex_work_id not in have {
            pos = "";
            for au in (w.get("authorships", []) or []) {
                if short_id(_s(au.get("author", {}) or {}, "id")) == p.openalex_author_id {
                    pos = _s(au, "author_position");
                }
            }
            p +>:Authored(author_position=pos):+> paper;
            have.append(paper.openalex_work_id);
        }
        for aw in (w.get("awards", []) or []) {
            aid = short_id(_s(aw, "id"));
            if aid and aid not in award_ids { award_ids.append(aid); }
        }
    }
    p.last_openalex_update = now_iso();
    return award_ids;
}

# ---------- grants ----------

def _person_name(person: dict) -> str {
    n = _s(person, "display_name");
    return n if n else (_s(person, "given_name") + " " + _s(person, "family_name")).strip();
}

def _upsert_grant(data: DataRoot, award: dict) -> Grant {
    aid = short_id(_s(award, "id"));
    grant: Grant | None = None;
    for g in [data ->:HasGrantIndex:->] {
        if g.openalex_award_id == aid { grant = g; break; }
    }
    if grant is None {
        grant = Grant(openalex_award_id=aid, created_at=now_iso());
        data +>:HasGrantIndex():+> grant;
    }
    amt = award.get("amount");
    grant.title = _s(award, "display_name");
    grant.funder_name = _s(award.get("funder", {}) or {}, "display_name");
    grant.funder_award_id = _s(award, "funder_award_id");
    grant.amount = float(amt) if isinstance(amt, (int, float)) else 0.0;
    grant.currency = _s(award, "currency");
    grant.start_date = _s(award, "start_date");
    grant.end_date = _s(award, "end_date");
    grant.landing_page_url = _s(award, "landing_page_url");
    grant.updated_at = now_iso();
    return grant;
}

def link_award(p: Professor, inst: Institution, award: dict) -> bool {
    people: list[list] = [];
    lead = award.get("lead_investigator");
    if isinstance(lead, dict) { people.append(["PI", lead]); }
    col = award.get("co_lead_investigator");
    if isinstance(col, dict) { people.append(["CO_PI", col]); }
    for c in (award.get("co_lead_investigators", []) or []) { people.append(["CO_PI", c]); }
    for i in (award.get("investigators", []) or []) { people.append(["INVESTIGATOR", i]); }
    awarded = _s(award.get("institution_awarded", {}) or {}, "id");
    role = "";
    for pair in people {
        person: dict = pair[1];
        porcid = short_id(_s(person, "orcid"));
        if p.orcid and porcid {
            if porcid == p.orcid { role = pair[0]; break; }
            continue;
        }
        if not names_match(p.name, _person_name(person)) { continue; }
        aff = person.get("affiliation", {}) or {};
        aff_name = _s(aff, "name").lower();
        same_inst = (aff_name and (aff_name == inst.name.lower() or inst.name.lower() in aff_name or aff_name in inst.name.lower()))
            or (awarded and short_id(awarded) == inst.openalex_institution_id);
        if same_inst { role = pair[0]; break; }
    }
    if not role { return False; }
    grant = _upsert_grant(get_data_root(), award);
    for e in [edge p ->:HasGrant:-> grant] {
        e.role = role;
        return True;
    }
    p +>:HasGrant(role=role):+> grant;
    return True;
}

def discover_grants(p: Professor, inst: Institution, award_ids: list[str]) -> int {
    linked = 0;
    for aid in award_ids[:25] {
        award = fetch_award(aid);
        if award is not None and link_award(p, inst, award) { linked += 1; }
    }
    p.last_grant_update = now_iso();
    return linked;
}

def institution_awards(inst: Institution) -> int {
    linked = 0;
    profs = [inst ->:HasProfessor:->];
    for award in fetch_institution_awards(inst.openalex_institution_id) {
        for p in profs {
            if link_award(p, inst, award) { linked += 1; }
        }
    }
    return linked;
}

# ---------- hiring ----------

def _store_hiring(p: Professor, quote: str, url: str, title: str) -> bool {
    existing = [p ->:HasHiringSignal:->];
    sig: HiringSignal | None = existing[0] if len(existing) > 0 else None;
    if sig is None {
        sig = HiringSignal();
        p +>:HasHiringSignal():+> sig;
    }
    ts = now_iso();
    sig.last_checked = ts;
    p.last_hiring_update = ts;
    if quote and sig.quote != quote { sig.date_found = ts; }
    sig.quote = quote;
    sig.source_url = url;
    sig.source_title = title;
    return quote != "";
}

def hiring_for(p: Professor, inst: Institution) -> bool {
    # 1) the professor's own pages; the quote must be verbatim on the fetched page
    for url in [p.lab_url, p.personal_url, p.faculty_url] {
        if not url { continue; }
        page = fetch_page(url);
        q = find_hiring_quote(page, p.name);
        if q { return _store_hiring(p, q, page.url, page.title or "Faculty page"); }
    }
    # 2) research LLM fallback; accepted only when verifiable on its source page
    res = research_hiring(p.name, inst.name, p.department, p.faculty_url, p.lab_url);
    rq = res.quote or "";
    ru = res.source_url or "";
    if rq and ru {
        src = fetch_page(ru);
        if quote_on_page(rq, src) {
            return _store_hiring(p, rq, src.url, res.source_title or src.title);
        }
    }
    return _store_hiring(p, "", "", "");
}

# ---------- orchestration ----------

def refresh_professor_cache(p: Professor) -> None {
    # Search reads these instead of walking papers/grants for every professor on every query.
    subs: list[str] = [];
    fields: list[str] = [];
    for paper in [p ->:Authored:->] {
        for s in [paper ->:HasSubfield:->] {
            if s.name not in subs { subs.append(s.name); }
            if s.field_name and s.field_name not in fields { fields.append(s.field_name); }
        }
    }
    subs.sort();
    fields.sort();
    p.cached_subfields = subs;
    p.cached_fields = fields;
    p.cached_grant_count = len([p ->:HasGrant:->]);
    p.cached_has_hiring = len([s for s in [p ->:HasHiringSignal:->] if s.quote]) > 0;
    refresh_search_text(p);
}

def refresh_search_text(p: Professor) -> None {
    # One normalized string per professor: name, title, department, university, fields, subfields.
    insts = [p <-:HasProfessor:<-];
    uni = insts[0].name if len(insts) > 0 else "";
    parts = [p.name, p.title, p.department, uni] + p.cached_fields + p.cached_subfields;
    p.search_text = normalize_name(" | ".join(parts));
}

def process_professor(p: Professor, inst: Institution) -> str {
    resolve_institution(inst);
    match_professor(p, inst);
    grants = 0;
    if p.openalex_author_id {
        award_ids = ingest_works(p);
        # Grants are paused (GRANTS_ENABLED=1 in .env turns them back on): each award is one
        # more OpenAlex call per professor and they were slowing the whole pipeline down.
        if os.environ.get("GRANTS_ENABLED", "0") == "1" {
            grants = discover_grants(p, inst, award_ids);
        }
    }
    # Hiring research needs an LLM key; without one we leave it unchecked (never "not hiring").
    if llm_ready() {
        hiring_for(p, inst);
    }
    p.pipeline_done = True;
    refresh_professor_cache(p);
    p.updated_at = now_iso();
    n_papers = len([p ->:Authored:->]);
    return f"{p.name}: {p.match_status}, {n_papers} papers, {grants} grants";
}

def:priv run_pipeline_for_professor(professor_id: str) -> PipelineResult {
    # Staff check here; the write itself runs in the shared context (see internal_run_professor).
    require_staff();
    try {
        r = fx.call_internal("internal_run_professor", {"professor_id": professor_id}) as dict;
        return PipelineResult(ok=bool(r.get("ok", True)), message=str(r.get("message", "")));
    } except Exception as e {
        return PipelineResult(ok=False, message="Pipeline failed: " + str(e)[:200]);
    }
}

def:pub internal_run_professor(worker_token: str = "", professor_id: str = "") -> PipelineResult {
    if not check_worker_token(worker_token) {
        return PipelineResult(ok=False, message="Forbidden");
    }
    p = find_professor(professor_id);
    if p is None { return PipelineResult(ok=False, message="Professor not found."); }
    inst = institution_of(p);
    if inst is None { return PipelineResult(ok=False, message="Professor has no institution."); }
    return PipelineResult(message=process_professor(p, inst));
}
