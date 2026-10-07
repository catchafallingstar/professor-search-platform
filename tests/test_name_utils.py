from services import name_utils as nu


def test_removable_credentials_are_not_part_of_identity():
    assert nu.clean_person_name("A.J. Bauer, Ph.D.") == "A.J. Bauer"
    assert nu.clean_person_name("Brendon Watson, Ph.D., M.D.") == "Brendon Watson"
    assert nu.clean_person_name("Ke'Andra Hagans, MSW, LCSW, SSW") == "Ke'Andra Hagans"
    assert nu.clean_person_name("MD Ari Blitz") == "Ari Blitz"


def test_real_name_tokens_are_preserved():
    assert nu.clean_person_name("A. B. Balantekin") == "A. B. Balantekin"
    assert nu.clean_person_name("Lin Ma") == "Lin Ma"
    assert nu.clean_person_name("Darnell Kaigler, Jr., D.D.S.") == "Darnell Kaigler, Jr."


def test_initials_can_expand_but_different_given_names_do_not_collapse():
    assert nu.names_match_strict("A.J. Bauer", "A. J. Bauer")
    assert nu.names_match_strict("A.J. Bauer", "Andrew J. Bauer")
    assert not nu.names_match_strict("Licheng Liu", "Lihong Liu")
