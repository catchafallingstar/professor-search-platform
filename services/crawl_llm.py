"""Directory discovery for universities without curated faculty URLs.

Uses the Jac `by llm()` function in services/crawler.jac (guess_directories) when an LLM
key is configured, then keeps only URLs on the university's own domain that actually
yield professors. Without a key it returns [] and the university is marked
NO_FACULTY_FOUND with a note saying how to add URLs.
"""

from services import fetchers as fx


def guess(name, website):
    if not fx.llm_configured() or not website:
        return []
    try:
        from services import crawler  # Jac module (importable once the Jac runtime is running)
    except Exception as e:
        print(f"[crawl_llm] crawler module unavailable: {e}")
        return []
    out = []
    for g in crawler.guess_directories(name, website):
        url = getattr(g, "url", "") or ""
        dept = getattr(g, "department", "") or "Faculty"
        if not url or not fx.same_domain(url, website):
            continue
        rows, _ = fx.extract_faculty_any(url, dept)
        if len(rows) >= 5:
            out.append([dept, url])
    return out
