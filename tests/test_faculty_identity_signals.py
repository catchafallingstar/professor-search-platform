from services import facultypage as fpg


def test_bauer_official_page_exposes_scholar_id():
    text = """
    # A.J. Bauer, Ph.D.
    Assistant Professor
    [Journalism & Creative Media](https://cis.ua.edu/academics/jcm/)
    [Google Scholar](https://scholar.google.com/citations?user=4lxq4hEAAAAJ&hl=en)
    """
    sig = fpg.identity_signals(text, name="A.J. Bauer")
    assert sig["scholar_ids"] == ["4lxq4hEAAAAJ"]


def test_balantekin_page_prefers_scholar_and_keeps_publications_link():
    text = """
    # A. B. Balantekin
    Eugene P. Wigner Professor
    [Publications](https://inspirehep.net/literature?page=1&q=f+a+balantekin)
    [Google Scholar](https://scholar.google.com/citations?hl=en&oi=ao&user=A2VBATUAAAAJ)
    """
    sig = fpg.identity_signals(text, name="A. B. Balantekin")
    assert sig["scholar_ids"] == ["A2VBATUAAAAJ"]
    assert sig["publication_pages"] == [
        "https://inspirehep.net/literature?page=1&q=f+a+balantekin"
    ]


def test_long_page_excerpt_does_not_drop_identity_links_near_bottom():
    text = ("navigation\n" * 5000) + (
        "\n[Google Scholar](https://scholar.google.com/citations?user=4lxq4hEAAAAJ&hl=en)\n"
        "## Publications\n- A real article title, 2025\n"
    )
    excerpt = fpg.identity_excerpt(text)
    assert "4lxq4hEAAAAJ" in excerpt
    assert "Publications" in excerpt
