"""Keep the directory across sandbox restarts.

export_directory(): graph -> plain JSON in data/directory.json (the synced project folder).
restore_directory(): JSON -> graph, only when the graph has no institutions yet.
Both run in the shared (guest) context, so the nodes they create are the shared database.
"""

import services.snapshot as snap;
import from services.models {
    DataRoot, Institution, Professor, FacultyPublicationAnchor, Paper, Subfield, Grant,
    HiringSignal, HasInstitution, HasProfessor, HasAnchor, Authored, HasSubfield, HasGrant,
    HasHiringSignal, HasSubfieldIndex, HasPaperIndex, HasGrantIndex, get_data_root, normalize_name
}
import from services.pipeline { upsert_subfield, refresh_professor_cache }
import from services.access { _registry }

glob INST_FIELDS: list[str] = [
    "name", "city", "state", "official_website", "ipeds_id", "openalex_institution_id", "ror_id",
    "priority_tier", "priority_rank", "priority_reason", "pipeline_state", "pipeline_note",
    "n_professors", "n_processed", "n_matched", "n_unresolved", "n_papers", "n_grants", "n_hiring",
    "created_at", "updated_at"
],
PROF_FIELDS: list[str] = [
    "name", "normalized_name", "title", "department", "institution_id", "faculty_url", "lab_url",
    "personal_url", "openalex_author_id", "orcid", "match_status", "pipeline_done",
    "cached_subfields", "cached_fields", "cached_grant_count", "cached_has_hiring",
    "profile_extracted", "match_method", "match_note", "created_at", "updated_at",
    "last_openalex_update", "last_grant_update", "last_hiring_update"
],
ANCHOR_FIELDS: list[str] = ["title", "doi", "publication_year", "url", "openalex_work_id", "used_for_match", "created_at"],
PAPER_FIELDS: list[str] = [
    "openalex_work_id", "doi", "title", "publication_date", "publication_year", "source_name",
    "paper_url", "citation_count", "created_at", "updated_at"
],
GRANT_FIELDS: list[str] = [
    "openalex_award_id", "title", "funder_name", "funder_award_id", "amount", "currency",
    "start_date", "end_date", "landing_page_url", "created_at", "updated_at"
],
HIRING_FIELDS: list[str] = ["quote", "source_url", "source_title", "date_found", "last_checked"];

def _pick(item: any, names: list[str]) -> dict {
    return {n: getattr(item, n) for n in names};
}

def _apply(item: any, d: dict, names: list[str]) -> None {
    for n in names {
        if n in d and d[n] is not None {
            setattr(item, n, d[n]);
        }
    }
}

def export_directory(force: bool = False) -> bool {
    data = get_data_root();
    insts: list[dict] = [];
    for i in [data ->:HasInstitution:->] {
        row = _pick(i, INST_FIELDS);
        profs: list[dict] = [];
        for p in [i ->:HasProfessor:->] {
            pr = _pick(p, PROF_FIELDS);
            pr["anchors"] = [_pick(a, ANCHOR_FIELDS) for a in [p ->:HasAnchor:->]];
            papers: list[dict] = [];
            for paper in [p ->:Authored:->] {
                pd = _pick(paper, PAPER_FIELDS);
                pd["subfields"] = [{"id": s.openalex_subfield_id, "name": s.name, "field": s.field_name} for s in [paper ->:HasSubfield:->]];
                papers.append(pd);
            }
            pr["papers"] = papers;
            grants: list[dict] = [];
            for g in [p ->:HasGrant:->] {
                gd = _pick(g, GRANT_FIELDS);
                role = "INVESTIGATOR";
                for ge in [edge p ->:HasGrant:-> g] { role = ge.role; }
                gd["role"] = role;
                grants.append(gd);
            }
            pr["grants"] = grants;
            sigs = [p ->:HasHiringSignal:->];
            pr["hiring"] = _pick(sigs[0], HIRING_FIELDS) if len(sigs) > 0 else None;
            profs.append(pr);
        }
        row["professors"] = profs;
        insts.append(row);
    }
    if len(insts) == 0 {
        return False;   # never overwrite a good snapshot with an empty graph
    }
    return bool(snap.save({"institutions": insts, "staff": list(_registry().emails)}, force));
}

def restore_directory() -> str {
    # MERGE, never skip: if the pipeline already created (some) universities after a reset,
    # the saved professors are added to them instead of the snapshot being ignored.
    data = get_data_root();
    existing: dict = {};
    for i in [data ->:HasInstitution:->] {
        existing[i.ipeds_id or i.name] = i;
    }
    raw = snap.load();
    if not isinstance(raw, dict) {
        return "No snapshot to restore.";
    }
    d: dict = raw as dict;
    reg = _registry();
    for e in (d.get("staff", []) or []) {
        if e and e not in reg.emails { reg.emails = reg.emails + [str(e)]; }
    }
    papers_by_id: dict = {};
    grants_by_id: dict = {};
    n_i = 0; n_p = 0;
    for row in (d.get("institutions", []) or []) {
        key = str(row.get("ipeds_id", "") or row.get("name", ""));
        inst: Institution | None = existing.get(key);
        if inst is None {
            inst = Institution();
            _apply(inst, row, INST_FIELDS);
            data +>:HasInstitution():+> inst;
            n_i += 1;
        }
        have = [x.normalized_name for x in [inst ->:HasProfessor:->]];
        for pr in (row.get("professors", []) or []) {
            if str(pr.get("normalized_name", "") or "") in have { continue; }   # already re-imported
            p = Professor();
            _apply(p, pr, PROF_FIELDS);
            inst +>:HasProfessor():+> p;
            n_p += 1;
            for a in (pr.get("anchors", []) or []) {
                an = FacultyPublicationAnchor();
                _apply(an, a, ANCHOR_FIELDS);
                p +>:HasAnchor():+> an;
            }
            for pd in (pr.get("papers", []) or []) {
                wid = str(pd.get("openalex_work_id", "") or "");
                paper: Paper | None = papers_by_id.get(wid) if wid else None;
                if paper is None {
                    paper = Paper();
                    _apply(paper, pd, PAPER_FIELDS);
                    data +>:HasPaperIndex():+> paper;
                    if wid { papers_by_id[wid] = paper; }
                    for s in (pd.get("subfields", []) or []) {
                        paper +>:HasSubfield():+> upsert_subfield(
                            data, str(s.get("id", "") or ""), str(s.get("name", "") or ""), str(s.get("field", "") or "")
                        );
                    }
                }
                p +>:Authored():+> paper;
            }
            for gd in (pr.get("grants", []) or []) {
                gid = str(gd.get("openalex_award_id", "") or "");
                gr: Grant | None = grants_by_id.get(gid) if gid else None;
                if gr is None {
                    gr = Grant();
                    _apply(gr, gd, GRANT_FIELDS);
                    data +>:HasGrantIndex():+> gr;
                    if gid { grants_by_id[gid] = gr; }
                }
                p +>:HasGrant(role=str(gd.get("role", "INVESTIGATOR") or "INVESTIGATOR")):+> gr;
            }
            hd = pr.get("hiring");
            if isinstance(hd, dict) {
                sig = HiringSignal();
                _apply(sig, hd, HIRING_FIELDS);
                p +>:HasHiringSignal():+> sig;
            }
            # Cheap cache fill from the snapshot data (no graph walks per professor; the old
            # refresh_professor_cache call here made restoring ~2000 professors take many minutes).
            subs: list[str] = [];
            flds: list[str] = [];
            for pd in (pr.get("papers", []) or []) {
                for s in (pd.get("subfields", []) or []) {
                    sn = str(s.get("name", "") or "");
                    fn = str(s.get("field", "") or "");
                    if sn and sn not in subs { subs.append(sn); }
                    if fn and fn not in flds { flds.append(fn); }
                }
            }
            subs.sort();
            flds.sort();
            p.cached_subfields = subs;
            p.cached_fields = flds;
            p.cached_grant_count = len(pr.get("grants", []) or []);
            p.cached_has_hiring = isinstance(hd, dict) and bool(hd.get("quote"));
            p.search_text = normalize_name(" | ".join([p.name, p.title, p.department, inst.name] + flds + subs));
        }
    }
    return f"Restored {n_i} universities and {n_p} professors from the saved snapshot.";
}
