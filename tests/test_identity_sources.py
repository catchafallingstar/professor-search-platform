import unittest

from services import name_utils as nu
from services import facultypage as fp


class NameNormalizationTests(unittest.TestCase):
    def test_removes_credentials_but_keeps_person(self):
        cases = {
            "A.J. Bauer, Ph.D.": "A.J. Bauer",
            "Brendon Watson, Ph.D., M.D.": "Brendon Watson",
            "MD Ari Blitz": "Ari Blitz",
            "ScD Xin Yu": "Xin Yu",
            "Fink, Jennifer T. PhD, MS": "Jennifer T. Fink",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(nu.clean_person_name(raw), expected)

    def test_initials_can_match_full_given_names(self):
        self.assertTrue(nu.names_match_strict("A.J. Bauer, Ph.D.", "Andrew J. Bauer"))
        self.assertTrue(nu.names_match_strict("A. B. Balantekin", "A. B. Balantekin"))

    def test_different_full_given_names_do_not_match(self):
        self.assertFalse(nu.names_match_strict("Licheng Liu", "Lihong Liu"))

    def test_generation_suffix_stays_distinct_in_storage(self):
        self.assertNotEqual(nu.storage_key("John Smith"), nu.storage_key("John Smith Jr."))
        self.assertTrue(nu.names_match_strict("John Smith Jr.", "John Smith"))


class FacultyProfileSignalTests(unittest.TestCase):
    def test_bauer_official_page_signals(self):
        text = """
# A.J. Bauer, Ph.D.
Assistant Professor
[Journalism & Creative Media](https://cis.ua.edu/academics/journalism-creative-media/)
[Google Scholar](https://scholar.google.com/citations?user=4lxq4hEAAAAJ&hl=en)
"""
        sig = fp.identity_signals(text, name="A.J. Bauer")
        self.assertEqual(sig["scholar_ids"], ["4lxq4hEAAAAJ"])
        self.assertEqual(fp.department_hint(text, "A.J. Bauer"), "Journalism & Creative Media")

    def test_balantekin_official_page_signals(self):
        text = """
# A. B. Balantekin
Eugene P. Wigner Professor
[Publications](https://inspirehep.net/authors/1017575)
[Google Scholar](https://scholar.google.com/citations?hl=en&oi=ao&user=A2VBATUAAAAJ)
"""
        sig = fp.identity_signals(text, name="A. B. Balantekin")
        self.assertEqual(sig["scholar_ids"], ["A2VBATUAAAAJ"])
        self.assertEqual(sig["publication_pages"], ["https://inspirehep.net/authors/1017575"])


if __name__ == "__main__":
    unittest.main()


class MetadataRegressionTests(unittest.TestCase):
    def test_page_headings_are_not_departments(self):
        from services import store as st
        for label in ("Faculty Directory", "People Directory", "Index.Html", "Www", "All", "Group", "Apply", "About"):
            with self.subTest(label=label):
                self.assertEqual(st.clean_department(label), "")
        self.assertEqual(st.clean_department("Engineering"), "Engineering")
        self.assertEqual(st.clean_department("Law"), "Law")

    def test_ece_uses_engineering_field_family_before_generic_computer(self):
        from services import pipe
        allowed = pipe._dept_fields("Electrical and Computer Engineering")
        self.assertIn("Materials Science", allowed)
        self.assertIn("Physics and Astronomy", allowed)
