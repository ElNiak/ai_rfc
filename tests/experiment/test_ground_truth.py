"""The aioquic ground-truth dataset and the anchors every entry claims.

This axis consults no model. Each entry names a file, a line and a value in a
pinned checkout, so the two anchor tests can resolve that triple against the
source itself: a dataset that passes them is ground truth in a way a judge's
opinion is not. The anchors skip rather than fail when the checkout is absent
or sits at a different commit, because the pinned tree is evidence kept
outside the repository.

The tests below the anchors are about the matcher rather than the dataset, and
the drafts they score are literals written in this file. A draft assembled
from the dataset's own statements would shrink with any mutation of the
dataset and go on scoring 1.0, so it would pin nothing.
"""

import re
from pathlib import Path

import pytest

from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.ground_truth import (
    EXCLUDED_DECIMAL,
    EXCLUDED_NOT_A_NUMBER,
    NEARBY_CHARS,
    load_dataset,
    resolve_anchors,
    score_draft,
)

pytestmark = pytest.mark.unit

CHECKOUT = Path.home() / "arfc-experiments" / "pristine" / "aioquic-w02-11" / "clone"

KINDS = {"constant", "error_code", "frame_type", "setting", "stream_type"}


def _checkout_commit(root: Path) -> str | None:
    """Resolve a checkout's HEAD commit without shelling out to git.

    Only a plain clone is handled, which is what the pinned tree is. A git
    worktree or submodule spells ``.git`` as a file holding ``gitdir: <path>``,
    which this function does not follow. A submodule's target is a complete
    gitdir, so following the pointer would be enough there; a linked worktree's
    is not, because it carries ``HEAD`` but reaches ``refs/`` and
    ``packed-refs`` through its ``commondir`` file, and a symbolic HEAD needs
    that second hop. Supporting either layout is deliberately left undone; the
    consequence is that the anchors skip with a reason reading ``is at None``,
    not that they pass.

    Args:
        root: The working tree whose ``.git`` directory is read.

    Returns:
        The 40-character commit hash, or ``None`` when no ref resolves.
    """
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    contents = head.read_text().strip()
    if not contents.startswith("ref: "):
        return contents
    ref = contents[len("ref: ") :]
    loose = root / ".git" / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = root / ".git" / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(f" {ref}"):
                return line.split(" ", 1)[0]
    return None


@pytest.fixture
def pinned_checkout() -> Path:
    """The pristine aioquic tree the dataset anchors into, or a skip."""
    dataset = load_dataset()
    if not CHECKOUT.is_dir():
        pytest.skip(f"the pinned aioquic checkout is absent at {CHECKOUT}")
    found = _checkout_commit(CHECKOUT)
    pinned = dataset["commit"]
    if found != pinned:
        pytest.skip(f"{CHECKOUT} is at {found}, the dataset pins {pinned}")
    return CHECKOUT


def test_every_entry_has_a_unique_id_and_the_required_fields():
    entries = load_dataset()["entries"]
    assert entries, "the dataset is empty"
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    for entry in entries:
        assert {
            "id",
            "statement",
            "symbol",
            "value",
            "source",
            "rfc",
            "kind",
        } <= entry.keys()
        assert {"path", "line"} <= entry["source"].keys()
        assert {"doc", "section"} <= entry["rfc"].keys()


def test_the_dataset_is_reachable_as_a_package_resource():
    """The YAML resolves through the package, not through a filesystem path.

    This runs against the source tree, where ``is_file()`` holds whenever the
    YAML sits beside ``__init__.py``, so it would stay green with the
    ``package-data`` entry deleted and says nothing about the wheel. The
    evidence for the wheel is the build measured in
    ``ai_rfc/experiment/groundtruth/__init__.py``: on setuptools 65.5.0 and
    CPython 3.10.12 the ``package-data`` entry is the load-bearing half, and
    dropping ``__init__.py`` does not by itself lose the YAML.
    """
    import importlib.resources as res

    assert (
        res.files("ai_rfc.experiment.groundtruth")
        .joinpath("aioquic-w02-11.yaml")
        .is_file()
    )


def test_every_value_and_symbol_is_text():
    """YAML reads an unquoted ``0x100`` as the integer 256.

    Coerced that way the anchor still fails, because ``256`` formats into the
    expected ``SYMBOL = VALUE`` string and no longer matches the line the
    source spells. What this guard buys is where the failure lands: it fails in
    the field that was coerced, naming the entry, rather than surfacing as a
    line mismatch that reads like a wrong citation. It also runs when the
    pinned checkout is absent and the anchors skip.
    """
    for entry in load_dataset()["entries"]:
        assert isinstance(entry["value"], str), entry["id"]
        assert isinstance(entry["symbol"], str), entry["id"]


def test_every_kind_comes_from_the_closed_vocabulary():
    for entry in load_dataset()["entries"]:
        assert entry["kind"] in KINDS, entry["id"]


def test_every_source_names_a_repo_relative_path_and_a_real_line_number():
    """Each ``source`` is usable against a checkout this repository does not own.

    The path half: the dataset travels in the wheel, so an absolute path or one
    that climbs out of the tree would not survive. The line half: the anchors
    index a list with it, so it has to be a genuine 1-based integer. ``bool`` is
    excluded explicitly because it subclasses ``int``, and ``line: true`` would
    otherwise pass here and then silently index line 1.
    """
    for entry in load_dataset()["entries"]:
        path = entry["source"]["path"]
        assert not path.startswith("/"), entry["id"]
        assert ".." not in Path(path).parts, entry["id"]
        line = entry["source"]["line"]
        assert isinstance(line, int) and not isinstance(line, bool), entry["id"]
        assert line >= 1, entry["id"]


def test_every_rfc_citation_names_a_document_and_a_section():
    """``section`` is matched as text, never coerced into text.

    An unquoted ``section: 8.10`` loads as the float 8.1, which ``str()`` would
    render as ``"8.1"`` and the pattern would accept -- the citation silently
    becomes Section 8.1. Requiring ``str`` is what keeps the section the
    document spells.
    """
    for entry in load_dataset()["entries"]:
        assert re.fullmatch(r"RFC \d+", entry["rfc"]["doc"]), entry["id"]
        section = entry["rfc"]["section"]
        assert isinstance(section, str), entry["id"]
        assert re.fullmatch(r"\d+(\.\d+)*", section), entry["id"]


def test_the_dataset_names_a_repository_and_a_commit_shaped_pin():
    """Only the shape is checked here.

    That the pin is the commit the anchors were read from is what the two
    anchor tests establish, by resolving every triple against a checkout
    standing at exactly this hash.
    """
    dataset = load_dataset()
    assert re.fullmatch(r"[0-9a-f]{40}", dataset["commit"])
    assert dataset["repository"]


def test_every_source_resolves_to_a_real_line(pinned_checkout):
    """Anchor 1: the cited ``file:line`` exists in the pinned checkout."""
    for entry in load_dataset()["entries"]:
        path = pinned_checkout / entry["source"]["path"]
        assert path.is_file(), f"{entry['id']}: {path} is not a file"
        lines = path.read_text().splitlines()
        line = entry["source"]["line"]
        assert 1 <= line <= len(lines), (
            f"{entry['id']}: {entry['source']['path']} has {len(lines)} lines, "
            f"the entry cites line {line}"
        )


def test_every_cited_line_spells_the_assignment_it_claims(pinned_checkout):
    """Anchor 2: the cited line is exactly ``SYMBOL = VALUE``, not merely near it.

    Equality rather than two substring checks, because substrings do not
    discriminate: ``0x10`` is contained in ``H3_MISSING_SETTINGS = 0x10A``, so a
    wrong error code would pass, and a citation moved to
    ``RESERVED_SETTINGS = (0x0, 0x2, 0x3, 0x4, 0x5)`` contains both ``SETTINGS``
    and ``0x4``, so a wrong line pointing at a different symbol of a different
    kind would pass too. All 41 entries cite a line that spells the assignment
    verbatim, so nothing legitimate needs the looser check.
    """
    for entry in load_dataset()["entries"]:
        path = pinned_checkout / entry["source"]["path"]
        text = path.read_text().splitlines()[entry["source"]["line"] - 1]
        location = f"{entry['source']['path']}:{entry['source']['line']}"
        expected = f"{entry['symbol']} = {entry['value']}"
        assert text.strip() == expected, (
            f"{entry['id']}: {location} is {text.strip()!r}, "
            f"which is not the assignment {expected!r} the entry claims"
        )


#: Every entry the matcher scores, stated once each. Written out here rather
#: than joined from the dataset's own ``statement`` fields: a draft derived
#: from the dataset shrinks with any mutation of it and goes on scoring 1.0,
#: which is an expectation that moves with the mutant and pins nothing.
#:
#: The settings carry the RFC's ``SETTINGS_`` prefix, which aioquic's symbols
#: do not. That is not decoration -- it is the case a symmetric word boundary
#: fails on, and with one it would be five permanent misses here.
#:
#: The line order is part of what this pins. Every claim sits beside its own
#: value, so the window reaches it whatever the neighbours are; reorder the
#: lines so that a symbol is further than ``NEARBY_CHARS`` from its own value
#: and the draft stops stating that fact, which is the matcher working and
#: not a regression in it.
#:
#: The four stream types are absent because they are no longer scored: their
#: values are bare decimal digits, which prose does not distinguish from any
#: other number, so they are excluded. Stating them here would not raise the
#: recall and would make this literal disagree with its own name.
STATES_EVERY_SCORED_ENTRY = """\
H3_DATAGRAM_ERROR is 0x33.
H3_NO_ERROR is 0x100.
H3_GENERAL_PROTOCOL_ERROR is 0x101.
H3_INTERNAL_ERROR is 0x102.
H3_STREAM_CREATION_ERROR is 0x103.
H3_CLOSED_CRITICAL_STREAM is 0x104.
H3_FRAME_UNEXPECTED is 0x105.
H3_FRAME_ERROR is 0x106.
H3_EXCESSIVE_LOAD is 0x107.
H3_ID_ERROR is 0x108.
H3_SETTINGS_ERROR is 0x109.
H3_MISSING_SETTINGS is 0x10A.
H3_REQUEST_REJECTED is 0x10B.
H3_REQUEST_CANCELLED is 0x10C.
H3_REQUEST_INCOMPLETE is 0x10D.
H3_MESSAGE_ERROR is 0x10E.
H3_CONNECT_ERROR is 0x10F.
H3_VERSION_FALLBACK is 0x110.
QPACK_DECOMPRESSION_FAILED is 0x200.
QPACK_ENCODER_STREAM_ERROR is 0x201.
QPACK_DECODER_STREAM_ERROR is 0x202.
The DATA frame has type 0x0.
The HEADERS frame has type 0x1.
aioquic names frame type 0x2 PRIORITY.
The CANCEL_PUSH frame has type 0x3.
The SETTINGS frame has type 0x4.
The PUSH_PROMISE frame has type 0x5.
The GOAWAY frame has type 0x7.
The MAX_PUSH_ID frame has type 0xD.
SETTINGS_QPACK_MAX_TABLE_CAPACITY is 0x1.
SETTINGS_MAX_FIELD_SECTION_SIZE is 0x6.
SETTINGS_QPACK_BLOCKED_STREAMS is 0x7.
SETTINGS_ENABLE_CONNECT_PROTOCOL is 0x8.
SETTINGS_H3_DATAGRAM is 0x33.
aioquic sends a DUMMY setting identifier 0x21.
"""


def test_resolve_anchors_agrees_with_the_two_anchor_tests(pinned_checkout):
    """The library form of what the two tests above assert entry by entry.

    They stay, and this does not replace them: they name the entry and the
    line that disagreed, which a tuple of ids cannot. What this adds is that
    the library form resolves the same 41 triples against the same checkout,
    so the two cannot drift apart. No verb calls it -- it is the resolver a
    caller outside this suite would reach for, and the point of pinning it
    here is that it already agrees with the assertions above before it has
    one.
    """
    assert resolve_anchors(load_dataset()["entries"], pinned_checkout) == ()


def test_resolve_anchors_names_every_entry_the_checkout_does_not_bear(tmp_path):
    """Every way an anchor fails, against a tree built here.

    No skip guard, because nothing in it is evidence kept outside the
    repository: an absent file, a line past the end, a line that spells a
    different assignment and a path that leaves the tree are properties of the
    resolver, not of aioquic.

    ``contained-value`` is the case that separates equality from a substring
    check. The other wrong value, ``0x3`` against ``Y = 0x2``, fails both, so
    a resolver mutated to ``entry["value"] not in line`` would report it and
    stay green -- which is the defect this row already met once, in the
    matcher. ``0x10`` *is* contained in ``Z = 0x10A``, so only equality
    reports it.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "connection.py").write_text("X = 0x1\nY = 0x2\nZ = 0x10A\n")
    entries = [
        {"id": "resolves", "symbol": "X", "value": "0x1", "source": _at(1)},
        {"id": "wrong-value", "symbol": "Y", "value": "0x3", "source": _at(2)},
        {"id": "contained-value", "symbol": "Z", "value": "0x10", "source": _at(3)},
        {"id": "past-the-end", "symbol": "X", "value": "0x1", "source": _at(9)},
        {
            "id": "no-such-file",
            "symbol": "X",
            "value": "0x1",
            "source": {"path": "src/absent.py", "line": 1},
        },
    ]
    assert resolve_anchors(entries, tmp_path) == (
        "wrong-value",
        "contained-value",
        "past-the-end",
        "no-such-file",
    )


def test_resolve_anchors_refuses_a_path_that_leaves_the_checkout(tmp_path):
    """An absolute path is not resolved against the clone -- it replaces it.

    ``clone / "/etc/passwd"`` is ``/etc/passwd``, which exists, is a file and
    would be read before anything noticed that its first line is not the
    assignment claimed. A ``..`` chain is the same hole spelled relatively.
    Both are reported unresolved, which is true of the checkout: it does not
    bear the anchor. The packaged dataset's own suite forbids both shapes, so
    this is a second boundary for the entries a caller assembles itself.
    """
    outside = tmp_path / "outside.py"
    outside.write_text("X = 0x1\n")
    clone = tmp_path / "clone"
    (clone / "src").mkdir(parents=True)
    (clone / "src" / "connection.py").write_text("X = 0x1\n")
    entries = [
        {"id": "inside", "symbol": "X", "value": "0x1", "source": _at(1)},
        {
            "id": "absolute",
            "symbol": "X",
            "value": "0x1",
            "source": {"path": str(outside), "line": 1},
        },
        {
            "id": "climbs-out",
            "symbol": "X",
            "value": "0x1",
            "source": {"path": "../outside.py", "line": 1},
        },
    ]
    assert resolve_anchors(entries, clone) == ("absolute", "climbs-out")


def _at(line: int) -> dict:
    """The ``source`` of an entry pointing at one line of the built tree."""
    return {"path": "src/connection.py", "line": line}


def test_a_value_coerced_out_of_text_is_refused_rather_than_scored():
    """The dataset's guard covers the dataset; this covers the boundary.

    ``score_draft`` takes a list of entries, and a caller can assemble one --
    the test above does. An entry whose value YAML read as the integer 256
    must not be scored: ``str(256)`` is a perfectly good decimal token, so it
    would be measured against the wrong number in the wrong base with nothing
    in the report saying so. The refusal names the entry and the field, not
    just the type.
    """
    entry = {"id": "coerced", "symbol": "H3_NO_ERROR", "value": 256}
    with pytest.raises(ExperimentError) as refused:
        score_draft("H3_NO_ERROR is 256.", [entry])
    assert "coerced" in str(refused.value)
    assert "value" in str(refused.value)


def test_a_draft_that_states_nothing_measures_a_zero_and_no_accuracy():
    """The distinction this axis exists to keep.

    ``recall`` is a *measured* zero: 35 entries were looked for and none of
    them found. ``claim_accuracy`` is not a zero at all -- the draft made no
    checkable claim, so there is nothing its accuracy could be the accuracy
    of. Written as 0.0 it would read as "every claim it made was wrong".
    """
    report = score_draft("", load_dataset()["entries"])
    assert report.scored == 35
    assert report.recall == 0.0
    assert report.claim_accuracy is None
    assert report.attempted == ()
    assert len(report.missed) == 35


def test_an_empty_dataset_scores_no_recall_rather_than_a_recall_of_zero():
    """The other half of the distinction above, and the one with no draft in it.

    A zero recall says entries were looked for and not found. With nothing to
    look for there is no such measurement, and 0.0 would report that a draft
    failed a test nobody set. The two cases are one line apart in the source
    and read identically in a report, so only this pins which is which.
    """
    report = score_draft("H3_NO_ERROR is 0x100.", [])
    assert report.scored == 0
    assert report.recall is None
    assert report.claim_accuracy is None


def test_the_entries_left_out_are_named_with_the_reason_each_is_left_out():
    """Excluded by how the value is spelled, never by the ``kind`` beside it.

    Six entries, for two reasons that are not interchangeable, so the report
    states which applies to each. Two are values no prose draft spells at all;
    four are the stream types, whose values are bare decimal digits. Naming
    them here is what makes the second exclusion a visible choice: an entry of
    any kind whose value is a hex literal is scored, and one of any kind whose
    value is a decimal numeral is not, and either drifting would fail this
    line rather than move the denominator in silence.
    """
    report = score_draft("", load_dataset()["entries"])
    assert report.excluded_because == (
        ("h3-const-alpn", EXCLUDED_NOT_A_NUMBER),
        ("h3-const-reserved-settings", EXCLUDED_NOT_A_NUMBER),
        ("h3-stream-control", EXCLUDED_DECIMAL),
        ("h3-stream-push", EXCLUDED_DECIMAL),
        ("h3-stream-qpack-encoder", EXCLUDED_DECIMAL),
        ("h3-stream-qpack-decoder", EXCLUDED_DECIMAL),
    )
    assert report.scored + len(report.excluded) == 41


def test_a_decimal_value_is_excluded_and_a_hex_one_of_the_same_kind_is_not():
    """The criterion is the value's spelling, pinned off the dataset.

    Every decimal entry the dataset carries today is a ``stream_type``, so an
    assertion made only against it cannot tell a rule about decimals from a
    rule about that ``kind`` -- which is the second dataset the exclusion was
    written to avoid becoming. These two entries share a ``kind`` and differ
    only in how the value is spelled.

    ``1024`` is deliberately not a single digit: a rule drawn at "one digit"
    would score it, and prose near a symbol carries numbers of every length.
    """
    entries = [
        {"id": "dec", "symbol": "WIDGET_A", "value": "1024", "kind": "setting"},
        {"id": "hex", "symbol": "WIDGET_B", "value": "0x400", "kind": "setting"},
    ]
    report = score_draft("WIDGET_A is 1024 and WIDGET_B is 0x400.", entries)
    assert report.excluded_because == (("dec", EXCLUDED_DECIMAL),)
    assert report.matched == ("hex",)
    assert report.scored == 1


def test_a_draft_stating_every_scored_entry_recalls_all_of_them():
    """The ceiling is reachable: 35 of 35, with nothing attempted in vain."""
    report = score_draft(STATES_EVERY_SCORED_ENTRY, load_dataset()["entries"])
    assert report.missed == ()
    assert report.mismatched == ()
    assert report.recall == 1.0
    assert report.claim_accuracy == 1.0


def test_a_symbol_beside_the_wrong_value_is_attempted_and_not_matched():
    """Without this the others pass on a matcher that matches anything.

    ``0x1000`` is the trap the anchor test met from the other side: it
    *contains* ``0x100``, so a substring check would read this draft as
    stating H3_NO_ERROR correctly. Comparing the nearby token as a number is
    what separates "states this fact" from "contains these characters".

    The wrong claim is a claim: the entry is attempted, so it is not among the
    missed, and it is the accuracy rather than the recall that carries it.
    """
    draft = "H3_NO_ERROR is 0x1000. H3_INTERNAL_ERROR is 0x102."
    report = score_draft(draft, load_dataset()["entries"])
    assert set(report.attempted) == {"h3-error-no-error", "h3-error-internal-error"}
    assert report.matched == ("h3-error-internal-error",)
    assert report.mismatched == ("h3-error-no-error",)
    assert "h3-error-no-error" not in report.missed
    assert report.claim_accuracy == 0.5
    assert report.recall == pytest.approx(1 / 35)


def test_a_value_further_away_than_the_window_is_not_a_claim_about_it():
    """Proximity is the whole of the predicate's notion of "about".

    The window is measured on the *normalised* text, and that is not a
    detail: :func:`_normalised` collapses any run of whitespace to one space,
    so a paragraph break costs a single character and two hundred blank lines
    between a symbol and a number leave them adjacent. What separates a claim
    from a coincidence here is therefore the count of intervening characters
    and nothing else -- which is why the filler below is a long unbroken run
    of words rather than the paragraph break a reader might picture.
    """
    entries = load_dataset()["entries"]
    filler = "filler " * ((NEARBY_CHARS // 7) + 4)
    assert len(filler) > NEARBY_CHARS
    report = score_draft(f"H3_NO_ERROR {filler} 0x100", entries)
    assert "h3-error-no-error" in report.missed
    assert report.attempted == ()
    assert report.claim_accuracy is None

    # The same two tokens inside the window are a claim, so what the assertion
    # above measures is the distance and not the phrasing.
    near = score_draft("H3_NO_ERROR 0x100", entries)
    assert near.matched == ("h3-error-no-error",)


def test_the_push_stream_type_is_now_excluded_rather_than_disambiguated():
    """``PUSH`` inside ``CANCEL_PUSH`` credited the push stream type.

    That was repaired by the disambiguation, and then the entry left the
    scored set altogether: its value is the bare decimal ``1``, and any ``1``
    within the window credited it whether or not the draft meant a stream
    type. So this sentence is now clean for two independent reasons, and the
    assertion is written on the second -- the entry is *excluded*, which is a
    stronger fact than "not attempted" and the one that is now load-bearing.

    The disambiguation itself is still live and still needed, for the
    ``SETTINGS`` inside ``H3_MISSING_SETTINGS`` and ``RESERVED_SETTINGS``; the
    two tests below it pin that. Nothing here should be read as saying it was
    retired.
    """
    draft = "A CANCEL_PUSH frame has type 0x3 and cancels exactly 1 promise."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-stream-push" in report.excluded
    assert "h3-stream-push" not in report.attempted
    assert "h3-frame-cancel-push" in report.matched


def test_an_error_code_named_after_a_frame_does_not_credit_the_frame():
    """``SETTINGS`` inside ``H3_MISSING_SETTINGS`` credited the SETTINGS frame.

    A draft discussing the error code and writing any hex number within the
    window was scored as having stated the frame type's value too -- here it
    even matched it, because the number beside the error code happens to be
    the frame type's. A real HTTP/3 draft names this error code.
    """
    draft = (
        "The error H3_MISSING_SETTINGS is 0x10A, raised when fewer than "
        "0x4 bytes arrive."
    )
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-frame-settings" not in report.attempted
    assert "h3-frame-settings" not in report.matched
    assert "h3-error-missing-settings" in report.matched


def test_an_excluded_entry_still_disambiguates_the_symbol_it_contains():
    """``SETTINGS`` inside ``RESERVED_SETTINGS`` credited the SETTINGS frame.

    ``RESERVED_SETTINGS`` is an entry whose value is a Python tuple, so it is
    excluded from scoring -- and it is exactly the symbol this draft writes.
    An entry that cannot be scored can still say which symbol a token belongs
    to, so the disambiguating set is drawn from every entry rather than from
    the scored ones.

    The second sentence is there so that the assertion cannot pass on a
    matcher that has stopped matching anything at all.
    """
    draft = (
        "The RESERVED_SETTINGS tuple is (0x0, 0x2, 0x3, 0x4, 0x5). "
        "H3_NO_ERROR is 0x100."
    )
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-frame-settings" not in report.attempted
    assert "h3-frame-settings" not in report.matched
    assert "h3-const-reserved-settings" in report.excluded
    assert "h3-error-no-error" in report.matched


def test_the_rfcs_settings_prefix_still_credits_aioquics_bare_symbol():
    """The behaviour the disambiguation must not take with it.

    The RFCs name every setting ``SETTINGS_X`` where aioquic's symbol is the
    bare ``X``, which is why nothing identifier-like may follow a symbol while
    a separator may precede it. ``SETTINGS_MAX_FIELD_SECTION_SIZE`` is not
    itself an entry's symbol, so it names no other entry and the credit
    stands. Replace the asymmetric boundary with a symmetric word boundary and
    this fails, along with the four other setting entries.
    """
    draft = "The setting SETTINGS_MAX_FIELD_SECTION_SIZE is 0x6, unlimited by default."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-setting-max-field-section-size" in report.matched


def test_a_symbol_coerced_out_of_text_is_refused_like_a_value():
    """The boundary guard covers both fields, and says what each one costs.

    The guard on ``value`` has a test of its own above. This is the other
    field it protects, and the harm is not the same one: a coerced value goes
    on being *scored*, against the wrong number in the wrong base, while a
    coerced symbol is what the draft is *searched for*, so the entry quietly
    stops matching any draft and reads as a miss. Drop the guard on the symbol
    -- coerce it with ``str()`` instead, which is all it would take -- and
    nothing anywhere says the entry has stopped being findable.
    """
    entry = {"id": "coerced", "symbol": 256, "value": "0x100"}
    with pytest.raises(ExperimentError) as refused:
        score_draft("H3_NO_ERROR is 0x100.", [entry])
    assert "coerced" in str(refused.value)
    assert "symbol" in str(refused.value)
    assert "miss" in str(refused.value)


def test_a_hex_run_an_identifier_leads_into_is_not_a_value():
    """The hex token's left guard, which nothing else pins.

    ``frame0x100`` is an identifier that merely ends in a hex-looking run, and
    reading a value out of it credits a draft for a name it wrote rather than
    a claim it made. Note what is *not* doing this work: keeping ``0x10`` out
    of ``0x10a`` is the greedy ``+``, and would survive both guards being
    deleted.
    """
    draft = "H3_NO_ERROR is documented in the frame0x100 dissector."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-error-no-error" in report.missed
    assert report.attempted == ()


def test_a_hex_run_an_identifier_continues_from_is_not_a_value():
    """The hex token's right guard, the mirror of the one above.

    ``0x10_bad`` is a fixture's name, not the number ``0x10``. Without the
    guard the run stops at the underscore and yields ``0x10``, which is a
    wrong value for this error code -- so the entry would be reported as an
    attempt the draft never made, moving the accuracy's denominator.
    """
    draft = "H3_MISSING_SETTINGS is 0x10_bad in the fixture."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-error-missing-settings" in report.missed
    assert report.attempted == ()


def test_a_symbol_a_longer_word_merely_ends_with_is_not_a_naming():
    """The symbol pattern's left guard, which the disambiguation cannot cover.

    ``DATA`` sits inside "metadata", which is an English word and no entry's
    symbol, so :func:`_inside_a_longer_symbol` has nothing to recognise it by
    -- only the lookbehind keeps the frame type out of this sentence. This is
    the guard the settings entries force to be a *lookbehind* on the character
    class rather than a word boundary: a word boundary here would take the
    five ``SETTINGS_X`` credits with it, which the test above pins.
    """
    draft = "The metadata section carries 0x0 bytes of padding."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-frame-data" in report.missed
    assert report.attempted == ()


@pytest.mark.parametrize(
    "draft",
    [
        "The flow control window starts at 0.",
        "The server may push 1 response.",
        "The QPACK decoder stream was written on 2026-09-03.",
        "HTTP/3 defines 2 unidirectional stream types the QPACK encoder uses.",
    ],
    ids=["quic-flow-control", "ordinary-english", "a-date", "a-count"],
)
def test_prose_that_claims_no_stream_type_is_credited_with_none(draft):
    """Why a bare decimal value cannot be evidence, in the drafts that showed it.

    Each of these was scored as a *correct* statement of a stream type before
    the exclusion: the flow-control sentence is about QUIC and claims nothing
    about stream types, "push 1 response" is ordinary English, and the date's
    ``03`` is a day of the month. Recall was being paid for non-claims, and
    the accuracy's denominator was counting claims no draft had made.

    The failure is not the window's width -- every one of these numbers is in
    the same clause as the symbol -- so no narrowing of it would have helped.
    """
    report = score_draft(draft, load_dataset()["entries"])
    assert report.attempted == ()
    assert report.matched == ()


def test_a_real_claim_beside_a_citation_is_still_credited():
    """The exclusion must not be paid for by refusing genuine claims.

    A draft of this document type cites ``RFC 9114`` on every page, and those
    four digits sit well inside the window of any symbol on the line. The
    frame type's value is hex, so the citation cannot be mistaken for it, and
    the claim the sentence really makes is credited.
    """
    draft = "The CANCEL_PUSH frame has type 0x3 (RFC 9114 Section 7.2.3)."
    report = score_draft(draft, load_dataset()["entries"])
    assert report.matched == ("h3-frame-cancel-push",)
    assert report.mismatched == ()
