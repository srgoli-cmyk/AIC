from app.titles import parse_title


def test_real_shaped_title():
    assert parse_title("Lecture 17 - The Chain Rule - LE [002-000-LE]") == (17, "002")


def test_case_insensitive_and_no_bracket():
    assert parse_title("lecture 3: limits") == (3, None)


def test_neither_present():
    assert parse_title("Review session") == (None, None)


def test_section_is_the_first_bracket_group_only():
    assert parse_title("Lecture 1 [abc] [002-000-LE]") == (1, None)
    assert parse_title("Lecture 1 [ 12-3] [002]") == (1, "12")


def test_lecture_number_too_large_for_integer_is_none():
    assert parse_title("Lecture 99999999999 - x [001]") == (None, "001")
    assert parse_title("Lecture 2147483647 - x") == (2147483647, None)
