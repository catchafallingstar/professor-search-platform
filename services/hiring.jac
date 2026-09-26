"""LLM hiring research. Returns a quote + source, or empty values when nothing explicit exists."""

obj HiringResearch {
    has quote: str | None = None,
        source_url: str | None = None,
        source_title: str | None = None;
}

sem HiringResearch.quote = "The exact verbatim sentence from a public page in which this professor or their lab says they are recruiting or accepting PhD students, postdocs, research assistants or other researchers. None if no such explicit statement is known. Generic descriptions of current lab members are NOT hiring statements.";
sem HiringResearch.source_url = "URL of the page containing the quote, or None.";
sem HiringResearch.source_title = "Title of that page (for example 'Join Our Lab'), or None.";

def research_hiring_llm(
    professor_name: str,
    university: str,
    department: str,
    faculty_url: str,
    lab_url: str
) -> HiringResearch by llm(temperature=0.0);

sem research_hiring_llm = "Find a current public statement from this professor or their lab indicating that they are looking for or accepting students, postdocs, research assistants, or other researchers. Return the exact relevant hiring quote and its source URL. The quote must explicitly indicate recruitment (e.g. 'We are currently looking for PhD students', 'We have openings for postdocs', 'Prospective students should contact Prof. X'). Never invent or paraphrase a quote. If you are not certain an explicit statement exists, return all fields as None.";
sem research_hiring_llm.professor_name = "Full name of the professor.";
sem research_hiring_llm.university = "University the professor works at.";
sem research_hiring_llm.department = "Department as listed by the university.";
sem research_hiring_llm.faculty_url = "Official faculty page URL.";
sem research_hiring_llm.lab_url = "Lab website URL if known, else empty.";

def research_hiring(
    professor_name: str,
    university: str,
    department: str,
    faculty_url: str,
    lab_url: str
) -> HiringResearch {
    try {
        result = research_hiring_llm(professor_name, university, department, faculty_url, lab_url);
        quote = (result.quote or "").strip();
        url = (result.source_url or "").strip();
        if not quote or not url or quote.lower() in ["null", "none"] {
            return HiringResearch();
        }
        return result;
    } except Exception as e {
        print(f"[hiring] LLM research failed: {str(e)}");
        return HiringResearch();
    }
}
