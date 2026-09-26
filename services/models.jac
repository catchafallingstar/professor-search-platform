"""Graph schema for the professor directory."""

node DataRoot {
    has label: str = "professor-directory";
}

node Institution {
    has name: str = "",
        city: str = "",
        state: str = "",
        official_website: str = "",
        ipeds_id: str = "",
        openalex_institution_id: str = "",
        ror_id: str = "",
        priority_tier: int = 3,
        priority_rank: int = 999,
        priority_reason: str = "",
        pipeline_state: str = "QUEUED",
        pipeline_note: str = "",
        created_at: str = "",
        updated_at: str = "";
}

node Professor {
    has name: str = "",
        normalized_name: str = "",
        title: str = "",
        department: str = "",
        institution_id: str = "",
        faculty_url: str = "",
        lab_url: str = "",
        personal_url: str = "",
        openalex_author_id: str = "",
        orcid: str = "",
        match_status: str = "PENDING",
        pipeline_done: bool = False,
        profile_extracted: bool = False,
        match_method: str = "",
        match_note: str = "",
        created_at: str = "",
        updated_at: str = "",
        last_openalex_update: str = "",
        last_grant_update: str = "",
        last_hiring_update: str = "";
}

node FacultyPublicationAnchor {
    has title: str = "",
        doi: str = "",
        publication_year: int = 0,
        url: str = "",
        openalex_work_id: str = "",
        used_for_match: bool = False,
        created_at: str = "";
}

node Paper {
    has openalex_work_id: str = "",
        doi: str = "",
        title: str = "",
        publication_date: str = "",
        publication_year: int = 0,
        source_name: str = "",
        paper_url: str = "",
        citation_count: int = 0,
        created_at: str = "",
        updated_at: str = "";
}

node Subfield {
    has openalex_subfield_id: str = "",
        name: str = "";
}

node Grant {
    has openalex_award_id: str = "",
        title: str = "",
        funder_name: str = "",
        funder_award_id: str = "",
        amount: float = 0.0,
        currency: str = "",
        start_date: str = "",
        end_date: str = "",
        landing_page_url: str = "",
        created_at: str = "",
        updated_at: str = "";
}

node HiringSignal {
    has quote: str = "",
        source_url: str = "",
        source_title: str = "",
        date_found: str = "",
        last_checked: str = "";
}

edge HasInstitution: DataRoot --> Institution {}
edge HasProfessor: Institution --> Professor {}
edge HasAnchor: Professor --> FacultyPublicationAnchor {}
edge Authored: Professor --> Paper {
    has author_position: str = "";
}
edge HasSubfield: Paper --> Subfield {}
edge HasGrant: Professor --> Grant {
    has role: str = "INVESTIGATOR";
}
edge HasHiringSignal: Professor --> HiringSignal {}
edge HasSubfieldIndex: DataRoot --> Subfield {}
edge HasPaperIndex: DataRoot --> Paper {}
edge HasGrantIndex: DataRoot --> Grant {}

def get_data_root() -> DataRoot {
    # Public commons graph: the same for every visitor and for the background worker.
    found = [root.shared -->][?:DataRoot];
    if len(found) > 0 {
        return found[0];
    }
    dr = root.shared ++> DataRoot();
    grant(dr, level=WritePerm);
    return dr;
}

def find_institution(iid: str) -> Institution | None {
    for i in [get_data_root() ->:HasInstitution:->] {
        if jid(i) == iid {
            return i;
        }
    }
    return None;
}

def find_professor(pid: str) -> Professor | None {
    for i in [get_data_root() ->:HasInstitution:->] {
        for p in [i ->:HasProfessor:->] {
            if jid(p) == pid {
                return p;
            }
        }
    }
    return None;
}

def now_iso() -> str {
    import from datetime { datetime, timezone }
    return datetime.now(timezone.utc).isoformat();
}

def normalize_name(name: str) -> str {
    import unicodedata;
    text = unicodedata.normalize("NFKD", name);
    text = "".join([c for c in text if not unicodedata.combining(c)]);
    text = text.lower().replace(".", " ").replace(",", " ").replace("-", " ");
    return " ".join(text.split());
}
