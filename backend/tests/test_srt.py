"""SRT parser tests on synthetic captions."""

from app.srt import Cue, parse_srt, parse_srt_with_stats


def test_basic_cues_and_order():
    raw = (
        "1\n00:00:01,000 --> 00:00:02,500\nHello there.\n\n2\n00:00:03,000 --> 00:00:04,000\nBye.\n"
    )
    assert parse_srt(raw) == [Cue(1.0, 2.5, "Hello there."), Cue(3.0, 4.0, "Bye.")]


def test_lines_are_joined_and_whitespace_collapsed():
    raw = "1\n00:00:00,000 --> 00:00:02,000\n  first   line \n\tsecond\u00a0line  \n"
    assert parse_srt(raw)[0].text == "first line second line"


def test_crlf_bom_dot_millis_and_missing_cue_numbers():
    raw = "\ufeff00:00:00.250 --> 00:00:01.5\r\nA\r\n\r\n00:00:02,000 --> 00:00:03,000\r\nB\r\n"
    assert parse_srt(raw) == [Cue(0.25, 1.5, "A"), Cue(2.0, 3.0, "B")]


def test_hours_over_99():
    raw = "1\n100:00:00,000 --> 100:00:01,000\nLong\n"
    assert parse_srt(raw) == [Cue(360000.0, 360001.0, "Long")]


def test_html_like_tags_are_stripped_but_comparisons_are_not():
    raw = '1\n00:00:00,000 --> 00:00:02,000\n<i>if a < b</i> then <font color="red">c</font>\n'
    assert parse_srt(raw)[0].text == "if a < b then c"


def test_malformed_timing_drops_that_cue_and_its_text_only():
    raw = (
        "1\n00:00:00,000 --> 00:00:01,000\nGood\n\n"
        "3\n00:00:xx --> 00:00:03,000\nBroken timing text\n\n"
        "4\n00:00:04,000 --> 00:00:05,000\nAlso good\n"
    )
    cues, dropped = parse_srt_with_stats(raw)
    assert [c.text for c in cues] == ["Good", "Also good"]
    assert dropped == 1


def test_end_before_start_and_empty_text_are_dropped():
    raw = (
        "1\n00:00:05,000 --> 00:00:04,000\nBackwards\n\n"
        "2\n00:00:06,000 --> 00:00:07,000\n<i> </i>\n\n"
        "3\n00:00:08,000 --> 00:00:09,000\nKept\n"
    )
    assert parse_srt(raw) == [Cue(8.0, 9.0, "Kept")]


def test_empty_and_nonsense_input():
    assert parse_srt("") == []
    assert parse_srt("not an srt file at all") == []


def test_absurd_hours_drop_only_that_cue():
    for hours in ("9" * 400, "9" * 5000, "1" + "0" * 36, "123456"):
        raw = (
            "1\n00:00:00,000 --> 00:00:01,000\nBefore\n\n"
            f"2\n{hours}:00:00,000 --> {hours}:00:01,000\nAbsurd\n\n"
            "3\n00:00:05,000 --> 00:00:06,000\nAfter\n"
        )
        cues, dropped = parse_srt_with_stats(raw)
        assert [c.text for c in cues] == ["Before", "After"], hours[:10]
        assert dropped == 1


def test_five_digit_hours_are_fine():
    cues = parse_srt("1\n99999:00:00,000 --> 99999:00:01,000\nOk\n")
    assert cues == [Cue(99999 * 3600.0, 99999 * 3600.0 + 1, "Ok")]


def test_out_of_order_cues_are_sorted_stably_by_start():
    raw = (
        "1\n00:01:40,000 --> 00:01:45,000\nlast\n\n"
        "2\n00:00:50,000 --> 00:00:55,000\nmiddle\n\n"
        "3\n00:00:00,000 --> 00:00:05,000\nfirst\n\n"
        "4\n00:00:50,000 --> 00:00:52,000\nmiddle two\n"
    )
    assert [c.text for c in parse_srt(raw)] == ["first", "middle", "middle two", "last"]


def test_cues_without_blank_lines_between_them():
    raw = (
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n"
        "2\n00:00:01,000 --> 00:00:02,000\nTwo\n"
        "3\n00:00:02,000 --> 00:00:03,000\nThree\n"
    )
    assert [c.text for c in parse_srt(raw)] == ["One", "Two", "Three"]


def test_nbsp_and_form_feed_separators():
    raw = (
        "1\n00:00:00,000 --> 00:00:01,000\nOne\n\u00a0\n"
        "2\n00:00:01,000 --> 00:00:02,000\nTwo\x0c"
        "3\n00:00:02,000 --> 00:00:03,000\nThree\n"
    )
    assert [c.text for c in parse_srt(raw)] == ["One", "Two", "Three"]


def test_blank_line_inside_cue_text_keeps_the_whole_text():
    raw = (
        "1\n00:00:00,000 --> 00:00:04,000\nfirst part\n\nsecond part\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nNext\n"
    )
    assert [c.text for c in parse_srt(raw)] == ["first part second part", "Next"]


def test_cue_number_is_removed_but_numbers_in_text_stay():
    raw = (
        "1\n00:00:00,000 --> 00:00:01,000\nThe answer is\n42\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\nOne more\n\n"
        "3\n00:00:02,000 --> 00:00:03,000\nlast line\n7\n"
    )
    # "42" has a cue number below it, "7" is last and not followed by a timing line: both stay.
    assert [c.text for c in parse_srt(raw)] == ["The answer is 42", "One more", "last line 7"]


def test_maths_is_not_eaten_by_tag_stripping():
    raw = "1\n00:00:00,000 --> 00:00:02,000\nif x <y and y> z then a < b\n"
    assert parse_srt(raw)[0].text == "if x <y and y> z then a < b"


def test_known_markup_and_style_marks_are_stripped_and_entities_unescaped():
    raw = (
        "1\n00:00:00,000 --> 00:00:02,000\n"
        '{\\an8}<i>Tom &amp; Jerry</i> <v Roger>hi</v> <c.yellow>x</c> <font color="red">y</font>\n'
    )
    assert parse_srt(raw)[0].text == "Tom & Jerry hi x y"


def test_escaped_markup_stays_visible_text():
    raw = "1\n00:00:00,000 --> 00:00:02,000\n&lt;i&gt; is italic\n"
    assert parse_srt(raw)[0].text == "<i> is italic"
