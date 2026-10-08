"""Faculty-page understanding with by llm(). Page fetching is plain Python (services/fetchers.py).

Every extraction is grounded in page text we fetched ourselves; hiring quotes must
appear verbatim on the fetched page.
"""

import services.fetchers as fx;

obj PageData {
    has url: str = "",
        title: str = "",
        text: str = "",
        ok: bool = False;
}

obj FacultyEntry {
    has name: str = "",
        title: str = "",
        department: str = "",
        profile_url: str = "";
}
sem FacultyEntry.name = "Full name exactly as listed on the page.";
sem FacultyEntry.title = "Academic title as listed (Professor, Associate Professor, Assistant Professor, etc.).";
sem FacultyEntry.department = "Department as stated by the page; use the department hint if the page does not say.";
sem FacultyEntry.profile_url = "Absolute URL of this person's individual faculty profile page from the page links, or empty.";

obj PublicationRef {
    has title: str = "",
        doi: str = "",
        year: int = 0,
        url: str = "";
}
sem PublicationRef.title = "Publication title exactly as written on the page.";
sem PublicationRef.doi = "DOI if shown on the page, else empty.";
sem PublicationRef.year = "Publication year if shown, else 0.";

obj ProfileExtract {
    has title: str = "",
        department: str = "",
        lab_url: str = "",
        personal_url: str = "",
        orcid: str = "",
        publications: list[PublicationRef] = [];
}
sem ProfileExtract.lab_url = "URL of the professor's research lab/group website if linked, else empty.";
sem ProfileExtract.personal_url = "URL of a personal homepage if linked, else empty.";
sem ProfileExtract.orcid = "ORCID iD (0000-0000-0000-0000) if shown, else empty.";
sem ProfileExtract.publications = "Up to 3 of the most recent publications listed on the page. Empty list if none. Never invent.";

obj DirectoryGuess {
    has department: str = "",
        url: str = "";
}

glob MAX_TEXT: int = 14000;

def fetch_page(url: str) -> PageData {
    d = fx.fetch_page(url) as dict[str, any];
    return PageData(url=str(d["url"]), title=str(d["title"]), text=str(d["text"]), ok=bool(d["ok"]));
}

def same_domain(url: str, website: str) -> bool {
    return bool(fx.same_domain(url, website));
}

def quote_on_page(quote: str, page: PageData) -> bool {
    a = str(fx.squash(quote));
    b = str(fx.squash(page.text));
    return page.ok and len(quote) >= 20 and a in b;
}

def extract_faculty_llm(page_text: str, page_url: str, department_hint: str) -> list[FacultyEntry] by llm(temperature=0.0);
sem extract_faculty_llm = "From this official university faculty directory page text (links shown as [text](url)), list every current faculty member with a professor-rank title (Professor, Associate Professor, Assistant Professor, Distinguished/Endowed Professor). Exclude staff, students, emeriti, adjuncts and lecturers. Only use people present in the text.";

def extract_profile_llm(page_text: str, professor_name: str) -> ProfileExtract by llm(temperature=0.0);
sem extract_profile_llm = "From this official faculty profile page text, extract the professor's title, department, lab website, personal homepage, ORCID and up to 3 of the most recent publications listed. Use only what appears in the text.";

def guess_directories_llm(university: str, website: str) -> list[DirectoryGuess] by llm(temperature=0.0);
sem guess_directories_llm = "Give up to 5 official faculty directory page URLs at this university for engineering, computer science and natural science departments. Only URLs on the university's own domain that you are confident exist.";

def extract_hiring_llm(page_text: str, professor_name: str) -> str by llm(temperature=0.0);
sem extract_hiring_llm = "Return the single exact sentence copied verbatim from the page text in which this professor or their lab says they are currently looking for, recruiting or accepting PhD students, postdocs, research assistants or other researchers. A description of current lab members is NOT a hiring statement. If there is no such sentence, return exactly NONE.";

def llm_ready() -> bool {
    return bool(fx.llm_configured());
}

def _rules_faculty(page: PageData, department_hint: str) -> list[FacultyEntry] {
    rows = fx.extract_faculty_rules({"ok": page.ok, "text": page.text}, department_hint) as list[dict[str, any]];
    return [
        FacultyEntry(name=str(r["name"]), title=str(r["title"]), department=str(r["department"]), profile_url=str(r["profile_url"]))
        for r in rows
    ];
}

def extract_faculty(page: PageData, department_hint: str) -> list[FacultyEntry] {
    if not page.ok { return []; }
    # Deterministic parser first: works without an LLM key and never invents people.
    found_rules = _rules_faculty(page, department_hint);
    if len(found_rules) > 0 or not llm_ready() {
        return found_rules;
    }
    try {
        found = extract_faculty_llm(page.text[:MAX_TEXT], page.url, department_hint);
        low = page.text.lower();
        return [f for f in found if f.name.strip() and f.name.lower() in low];
    } except Exception as e {
        print(f"[crawler] faculty extraction failed: {str(e)}");
        return [];
    }
}

def extract_profile(page: PageData, professor_name: str) -> ProfileExtract | None {
    if not page.ok or not llm_ready() { return None; }
    try {
        res = extract_profile_llm(page.text[:MAX_TEXT], professor_name);
        res.publications = res.publications[:3];
        return res;
    } except Exception as e {
        print(f"[crawler] profile extraction failed: {str(e)}");
        return None;
    }
}

def guess_directories(university: str, website: str) -> list[DirectoryGuess] {
    if not llm_ready() { return []; }
    try {
        return guess_directories_llm(university, website)[:5];
    } except Exception as e {
        print(f"[crawler] directory guess failed: {str(e)}");
        return [];
    }
}

def find_hiring_quote(page: PageData, professor_name: str) -> str {
    if not page.ok or not llm_ready() { return ""; }
    q = "";
    try {
        q = extract_hiring_llm(page.text[:MAX_TEXT], professor_name).strip().strip("\"");
    } except Exception as e {
        print(f"[crawler] hiring extraction failed: {str(e)}");
        return "";
    }
    if not q or q.upper().startswith("NONE") {
        return "";
    }
    return q if quote_on_page(q, page) else "";
}
