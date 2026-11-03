"""Chunker tests on synthetic cues."""

from app.chunking import MAX_CHARS, MAX_WINDOW, OVERLAP, chunk_cues
from app.srt import Cue


def run(n, dur=5.0, gap=0.0, sentence_ends=(), gaps=None):
    """n contiguous cues of `dur` seconds; `gaps` maps a cue index to a pause before it."""
    cues, t = [], 0.0
    for i in range(n):
        t += (gaps or {}).get(i, gap)
        text = f"w{i}." if i in sentence_ends else f"w{i}"
        cues.append(Cue(t, t + dur, text))
        t += dur
    return cues


def test_no_cues_no_chunks():
    assert chunk_cues([]) == []


def test_lecture_shorter_than_45s_is_one_chunk():
    cues = run(6)  # 30 s
    chunks = chunk_cues(cues)
    assert len(chunks) == 1
    assert (chunks[0].start, chunks[0].end) == (0.0, 30.0)
    assert chunks[0].text == "w0 w1 w2 w3 w4 w5"


def test_cut_at_first_sentence_end_after_45s():
    cues = run(30, sentence_ends={5, 9, 10})  # cue 5 ends at 30 s: too early to cut
    first = chunk_cues(cues)[0]
    assert (first.start, first.end) == (0.0, 50.0)  # cue 9 is the first end at or after 45 s


def test_cut_after_a_pause_over_1_5s():
    cues = run(30, gaps={10: 2.0})  # 2 s pause before cue 10
    first = chunk_cues(cues)[0]
    assert first.end == 50.0 and first.text.endswith("w9")
    short_pause = chunk_cues(run(30, gaps={10: 1.5}))[0]
    assert short_pause.end == 56.5  # 1.5 s is not "over": forced cut, next cue would pass 60 s


def test_sentence_end_with_closing_quote_or_bracket():
    cues = run(30)
    cues[9] = Cue(cues[9].start, cues[9].end, 'He said "stop."')
    assert chunk_cues(cues)[0].end == 50.0
    cues[9] = Cue(cues[9].start, cues[9].end, "(see above.)")
    assert chunk_cues(cues)[0].end == 50.0


def test_forced_cut_keeps_windows_within_60s():
    chunks = chunk_cues(run(60))  # 300 s with no sentence ends and no pauses
    assert chunks[0].end == 60.0
    assert all(c.end - c.start <= MAX_WINDOW for c in chunks)


def test_overlap_is_whole_cues_of_about_10s_and_never_the_whole_chunk():
    cues = run(30, sentence_ends={9})
    a, b = chunk_cues(cues)[:2]
    assert a.end == 50.0
    assert b.start == 40.0  # cues 8 and 9: 10 s of overlap
    assert a.end - b.start <= OVERLAP


def test_all_cues_covered_in_order_with_exact_boundaries():
    cues = run(97, sentence_ends={7, 31, 33, 60}, gaps={20: 3.0, 70: 2.0})
    chunks = chunk_cues(cues)
    assert [(c.start, c.end) for c in chunks] == [
        (0.0, 60.0),
        (50.0, 100.0),
        (90.0, 148.0),
        (138.0, 198.0),
        (188.0, 248.0),
        (238.0, 298.0),
        (288.0, 348.0),
        (338.0, 395.0),
        (385.0, 445.0),
        (435.0, 490.0),
    ]
    assert all(c.text for c in chunks)
    covered = {w for c in chunks for w in c.text.replace(".", "").split()}
    assert covered == {f"w{i}" for i in range(97)}


def test_single_cue_longer_than_60s_is_its_own_chunk_without_overlap():
    cues = (
        run(4)
        + [Cue(20.0, 120.0, "long cue")]
        + [Cue(120.0 + 5 * i, 125.0 + 5 * i, f"t{i}") for i in range(3)]
    )
    chunks = chunk_cues(cues)
    long = [c for c in chunks if "long cue" in c.text]
    assert len(long) == 1 and long[0].text == "long cue"
    assert (long[0].start, long[0].end) == (20.0, 120.0)
    before = chunks[chunks.index(long[0]) - 1]
    after = chunks[chunks.index(long[0]) + 1]
    assert before.end <= 20.0 and after.start == 120.0  # no overlap into or out of it


def test_short_final_remainder_is_allowed_and_tail_adds_no_duplicate_chunk():
    cues = run(11, sentence_ends={8})  # 55 s; cut after cue 8 (45 s), cues 9-10 remain
    chunks = chunk_cues(cues)
    assert [c.end for c in chunks] == [45.0, 55.0]
    assert chunks[1].start == 35.0  # 10 s of overlap + 2 new cues


def test_deterministic():
    cues = run(50, sentence_ends={9, 22, 40})
    assert chunk_cues(cues) == chunk_cues(cues)


def test_overlap_is_the_whole_cue_amount_closest_to_10s():
    uniform = [Cue(6.0 * i, 6.0 * i + 6, f"w{i}") for i in range(30)]
    chunks = chunk_cues(uniform)
    assert (chunks[0].end, chunks[1].start) == (60.0, 48.0)  # 6 s cues: 12 s beats 6 s and 18 s


def test_single_last_cue_over_10s_is_kept_as_overlap():
    cues = [Cue(7.0 * i, 7.0 * i + 7, f"w{i}") for i in range(7)] + [Cue(49, 60, "eleven")]
    cues += [Cue(60 + 5 * i, 65 + 5 * i, f"t{i}") for i in range(20)]
    chunks = chunk_cues(cues)
    assert chunks[0].end == 60.0
    assert chunks[1].start == 49.0  # the 11 s cue alone; adding the 7 s cue before it would be 18


def test_no_overlap_across_a_long_pause():
    cues = run(10, sentence_ends={9}) + [
        Cue(100.0 + 5 * i, 105.0 + 5 * i, f"p{i}") for i in range(4)
    ]
    chunks = chunk_cues(cues)
    assert chunks[0].end == 50.0
    assert chunks[1].start == 100.0  # 50 s of silence: the chunk starts at the speech


def test_pause_before_45s_does_not_cut():
    chunks = chunk_cues(run(30, gaps={4: 3.0}))  # a 3 s pause at 20 s
    assert chunks[0].end > 45.0
    assert "w4" in chunks[0].text


def test_out_of_order_cues_give_ordered_chunks_with_true_end():
    cues = [Cue(100, 105, "c"), Cue(50, 55, "b"), Cue(0, 5, "a")]
    chunks = chunk_cues(cues)
    assert [(c.start, c.end, c.text) for c in chunks] == [(0, 55, "a b"), (100, 105, "c")]


def test_long_cue_overlapping_later_cues_is_alone_and_end_is_the_latest_end():
    cues = [Cue(0, 100, "long"), Cue(5, 10, "b"), Cue(10, 15, "c")]
    chunks = chunk_cues(cues)
    assert [(c.start, c.end, c.text) for c in chunks] == [(0, 100, "long"), (5, 15, "b c")]


def test_zero_length_cues_are_capped_by_characters():
    cues = [Cue(0.0, 0.0, f"word{i}") for i in range(20_000)]
    chunks = chunk_cues(cues)
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    assert len(chunks) < 400  # linear: overlap does not make each chunk repeat the last
    covered = {w for c in chunks for w in c.text.split()}
    assert len(covered) == 20_000


def test_chunks_of_zero_length_cues_stay_in_range():
    chunks = chunk_cues([Cue(3.0, 3.0, "a"), Cue(3.0, 3.0, "b")])
    assert [(c.start, c.end, c.text) for c in chunks] == [(3.0, 3.0, "a b")]
