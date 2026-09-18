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
The control stream has stream type 0.
The push stream has stream type 1.
The QPACK encoder stream has stream type 2.
The QPACK decoder stream has stream type 3.
"""


def test_resolve_anchors_agrees_with_the_two_anchor_tests(pinned_checkout):
    """The library form of what the two tests above assert entry by entry.

    They stay, and this does not replace them: they name the entry and the
    line that disagreed, which a tuple of ids cannot. What this adds is that
    the function the verb's callers use resolves the same 41 triples against
    the same checkout, so the two cannot drift apart.
    """
    assert resolve_anchors(load_dataset()["entries"], pinned_checkout) == ()


def test_resolve_anchors_names_every_entry_the_checkout_does_not_bear(tmp_path):
    """All three ways an anchor fails, against a tree built here.

    No skip guard, because nothing in it is evidence kept outside the
    repository: an absent file, a line past the end and a line that spells a
    different assignment are properties of the resolver, not of aioquic.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "connection.py").write_text("X = 0x1\nY = 0x2\n")
    entries = [
        {"id": "resolves", "symbol": "X", "value": "0x1", "source": _at(1)},
        {"id": "wrong-value", "symbol": "Y", "value": "0x3", "source": _at(2)},
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
        "past-the-end",
        "no-such-file",
    )


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

    ``recall`` is a *measured* zero: 39 entries were looked for and none of
    them found. ``claim_accuracy`` is not a zero at all -- the draft made no
    checkable claim, so there is nothing its accuracy could be the accuracy
    of. Written as 0.0 it would read as "every claim it made was wrong".
    """
    report = score_draft("", load_dataset()["entries"])
    assert report.scored == 39
    assert report.recall == 0.0
    assert report.claim_accuracy is None
    assert report.attempted == ()
    assert len(report.missed) == 39


def test_the_entries_left_out_are_the_ones_whose_value_is_no_number():
    """Excluded by the shape of the value, never by the ``kind`` beside it.

    The two are the ``kind: constant`` entries today, and naming them here is
    what makes that a visible choice: a constant whose value *is* a number
    would be scored, and a future entry of any kind whose value is a Python
    literal would be excluded, and either would fail this line rather than
    move the denominator in silence.
    """
    report = score_draft("", load_dataset()["entries"])
    assert report.excluded == ("h3-const-alpn", "h3-const-reserved-settings")
    assert report.scored + len(report.excluded) == 41


def test_a_draft_stating_every_scored_entry_recalls_all_of_them():
    """The ceiling is reachable: 39 of 39, with nothing attempted in vain."""
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
    assert report.recall == pytest.approx(1 / 39)


def test_a_value_further_away_than_the_window_is_not_a_claim_about_it():
    """Proximity is the whole of the predicate's notion of "about".

    A draft holding the symbol in one paragraph and the number in another
    states no relation between them, and a matcher that read one anyway would
    score a draft for the words it happens to contain.
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


def test_a_symbol_the_draft_wrote_only_inside_a_longer_one_is_not_a_claim():
    """``PUSH`` inside ``CANCEL_PUSH`` credited the push stream type.

    The prefix side of the symbol boundary is deliberately loose, so the
    ``push`` of ``cancel_push`` matched the stream type's bare ``PUSH`` and the
    ``1`` of the same sentence sat well inside the window. The draft says
    nothing about stream types, so both the recall and the accuracy were being
    moved by a claim it never made.

    The sentence cancels "exactly 1 promise" rather than the "1 push id" the
    frame really carries, because a bare ``push`` written beside a ``1`` is a
    mention of the stream type as far as any matcher over prose can tell. That
    one is the window's limitation, not this one, and no rule drawn from the
    dataset removes it.
    """
    draft = "A CANCEL_PUSH frame has type 0x3 and cancels exactly 1 promise."
    report = score_draft(draft, load_dataset()["entries"])
    assert "h3-stream-push" not in report.attempted
    assert "h3-stream-push" not in report.matched
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
