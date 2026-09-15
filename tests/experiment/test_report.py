import copy

import pytest

from ai_rfc.experiment.report import render_report

#: One representative of each character that ends a line. CR and LF are
#: CommonMark's two line endings, so either one ends a GFM table row, and the
#: rest additionally split :meth:`str.splitlines`, which the assertions below
#: call. Seven characters are a *sample* of the category, never the category:
#: that is why the renderer's fix is :func:`ai_rfc.driver.printable`'s
#: predicate over ``isprintable`` rather than a list like this one. Written
#: with :func:`chr` so the source carries no unprintable character of its own,
#: which ``tests/substrate/test_source_hygiene.py`` forbids.
LINE_BREAKERS = (
    "\r",
    "\n",
    chr(0x0B),
    chr(0x0C),
    chr(0x85),
    chr(0x2028),
    chr(0x2029),
)


def _aggregate(*, target="fixture", run_id="A1", cluster_id="c0002-x"):
    """The one campaign aggregate every test here renders.

    The three values are parameters rather than literals because each reaches
    the report by a different route — a code span, a table cell, and a row
    key — so a test of what the renderer does to a hostile value has to be
    able to say which route it is testing.

    Args:
        target: The campaign's target, rendered as a code span.
        run_id: The key of ``runs`` and the run's own ``run_id``.
        cluster_id: The key of ``pass_k`` and the cluster's own ``cluster_id``.

    Returns:
        The aggregate record, shaped as ``metrics.analyze_campaign`` builds it.
    """
    cluster = {
        "cluster_id": cluster_id,
        "ordinal": 2,
        "completed": True,
        "artifacts": True,
    }
    run = {
        "run_id": run_id,
        "arm": "A",
        "repeat": 1,
        "status": {"exit_code": 0, "timed_out": False},
        "window_size": 1,
        "clusters": [cluster],
        "artifacts_fraction": 1.0,
        "completed_fraction": 1.0,
        "gates": {"manifest_exit": 0, "citation_exit": 0, "clean": True},
        "cost": {
            "total_cost_usd": 1.25,
            "num_turns": 7,
            "duration_ms": 7000,
            "usage": {
                "input_tokens": 700,
                "output_tokens": 140,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
        "trajectory": {
            "auc": 0.4,
            "tokens_to_first_completion": 500,
            "total_tokens": 840,
            "points": [],
        },
        "audit": {
            "integrity": True,
            "bypass_attempts": {"count": 0},
            "errors": {"class1": 0, "class2": 0},
        },
    }
    arm = {
        "runs": 1,
        "completed_fraction_mean": 1.0,
        "completed_fraction_min": 1.0,
        "artifacts_fraction_mean": 1.0,
        "gates_clean_runs": 1,
        "pass_k": {cluster_id: True},
        "pass_k_mean": 1.0,
        "integrity_rate": 1.0,
        "bypass_attempts": 0,
        "errors_class1": 0,
        "errors_class2": 0,
        "hand_edits": 0,
        "cost_total": 1.25,
        "cost_mean": 1.25,
        "failure_cost_share": 0.0,
        "cost_per_completed_cluster": 1.25,
        "tokens_to_first_completion_mean": 500.0,
        "auc_mean": 0.4,
        "timed_out_runs": 0,
        "nonzero_exit_runs": 0,
    }
    return {
        "campaign": "pilot-test",
        "target": target,
        "window": [2, 2],
        "model": "m",
        "effort": "high",
        "claude_version": "fake-claude 0.0.0",
        "git": {"panther": "abc", "ai_rfc": "def"},
        "parity_pre_run": {"passed": True, "summary": "ok"},
        "run_order": ["A1"],
        "runs": {run_id: run},
        "arms": {"A": arm},
        "definitions": {"completed": "artifacts AND gates"},
    }


def test_render_report_has_every_section_and_the_numbers():
    text = render_report(_aggregate())
    assert text.startswith("# Campaign pilot-test\n")
    for heading in (
        "## Per arm",
        "## Per run",
        "## Per cluster (pass^k)",
        "## Definitions",
    ):
        assert heading in text
    assert "| A | 1 | 1.000 / 1.000 |" in text
    assert "| A1 | A | 0 | no | 1/1 |" in text
    assert "| c0002-x | ✓ |" in text
    assert "- **completed**: artifacts AND gates" in text
    # The retired `git.panther` was asserted here too, as the fixture's "abc";
    # the test below now owns that value, asserting it is *not* printed.
    assert "fake-claude 0.0.0" in text


def test_the_report_ignores_an_archived_records_panther_revision():
    """The fixture aggregate is an archived one: its ``git`` still has both.

    The harness stopped taking a PANTHER checkout, so the value it used to
    print under that label was a description of this package's own root. The
    renderer now names only what it can name truthfully, and reads straight
    past the retired key rather than refusing a record that carries it.
    """
    text = render_report(_aggregate())

    assert "- git: ai_rfc `def`\n" in text
    assert "PANTHER" not in text
    assert "abc" not in text


def test_render_report_tolerates_missing_values():
    aggregate = _aggregate()
    aggregate["arms"]["A"]["cost_per_completed_cluster"] = None
    aggregate["runs"]["A1"]["audit"] = None
    text = render_report(aggregate)
    assert "—" in text


def test_an_undecided_cluster_is_not_rendered_as_a_failure():
    aggregate = _aggregate()
    aggregate["arms"]["A"]["pass_k"] = {"c0002-x": None}
    aggregate["arms"]["A"]["pass_k_mean"] = None
    text = render_report(aggregate)
    assert "| c0002-x | \u2014 |" in text
    assert "\u2717" not in text


def test_a_backtick_in_a_value_cannot_break_its_code_span():
    """A target carrying a backtick must not escape its span into raw markup.

    The line that is now `report.py:164` emitted a single-backtick span, so a
    backtick in the value closed it early and the rest went live as markup.
    `_code` fences with a run one longer than the longest run inside, so the
    span opens with two.
    """
    aggregate = _aggregate(target="repo`<b>bold</b>`x")
    target_line = next(
        line
        for line in render_report(aggregate).splitlines()
        if line.startswith("- target:")
    )
    assert target_line.startswith("- target: ``")


def test_a_pipe_in_a_cluster_id_cannot_add_a_column():
    """cluster_id reaches a table cell and is derived from repo history."""
    aggregate = _aggregate(cluster_id="c1|evil|x")
    rows = [
        line
        for line in render_report(aggregate).splitlines()
        if line.startswith("| c1")
    ]
    assert len(rows) == 1
    # Structural pipes are one per arm column plus the two outer rails.
    assert rows[0].count("|") - rows[0].count("\\|") == len(aggregate["arms"]) + 2


def test_a_newline_in_a_run_id_cannot_break_the_table():
    aggregate = _aggregate(run_id="r1\nr2")
    assert "r1\nr2" not in render_report(aggregate)


#: A revision map the YAML parser refused, shaped as ``quality._revision_map``
#: reports it: the path it prefixes, the parser's own two location lines, and
#: the caret it draws under the offending column. Six lines and a pipe, which
#: is the point — this is the one value in the payload that is multi-line free
#: text, and a table cell is a single line by construction.
YAML_REFUSAL = (
    "/w/revisions.yaml: while parsing a block mapping\n"
    '  in "<unicode string>", line 2, column 3:\n'
    "      nope: a | b\n"
    "      ^\n"
    "expected <block end>, but found '?'\n"
    '  in "<unicode string>", line 4, column 3'
)

#: A tag the draft repository would not yield a draft at, as
#: ``quality._draft_at`` reports it. Measured, not invented: this is what
#: ``draft.gate.draft_text`` actually raised for this ref, interpolating git's
#: own stderr. One line — every arm of that function reachable from here
#: produced one — so what this value tests is the *pipe*, which is real twice
#: over, the ref being a tag name read out of an agent-writable
#: ``revisions.yaml``. The multi-line case is :data:`YAML_REFUSAL`'s.
GATE_REFUSAL = (
    "draft-x|02: could not list its tree: " "fatal: Not a valid object name draft-x|02"
)


def _revision(**overrides):
    """One reduced lint row, carrying the fields the report reads.

    Args:
        **overrides: Fields to replace in the measured baseline.

    Returns:
        The row.
    """
    row = {
        "tag": "draft-x-00",
        "number": 0,
        "cluster_id": "c0001-a",
        "kind": "cluster",
        "manifest_status": "read",
        "draft_status": "read",
        "draft_error": None,
        "manifest_error": None,
        "citations": {"cited_fraction": 0.5},
        "abstract": {"word_count": 40},
        "narration_count": 2,
        "finding_count": 3,
    }
    row.update(overrides)
    return row


def _quality_aggregate():
    """``_aggregate`` plus the two quality shapes its own run cannot show.

    Three runs, one per state the section must keep apart — because an
    assertion about any one of them proves nothing unless another run in the
    same render is in a different state:

    * ``A1`` is ``_aggregate``'s own run and carries no ``quality`` key at
      all, which is an aggregate archived before the instrument existed. It is
      left exactly as it is, since it is also what the older tests render.
    * ``B1`` enumerated its map and has three revisions — one measured against
      a frozen manifest, one whose manifest was missing, and one the draft
      repository would not yield any text for — and it is the run whose draft
      was built.
    * ``C1`` has a map the parser refused, so its revision count is unknown
      rather than zero, and its error is six lines of free text. Its build was
      never asked for, so every build column of its row is unmeasured.

    Each of the three free-text columns carries a pipe here. A cluster id
    reaches the frozen manifest's path and so reaches ``manifest_error``, and
    a tag reaches ``draft_error``; both come out of files an arm can write.

    Returns:
        The aggregate record.
    """
    aggregate = _aggregate()
    qualities = {
        "B1": {
            "revisions": [
                _revision(),
                _revision(
                    tag="draft-x-01",
                    number=1,
                    cluster_id="c0002|b",
                    manifest_status="missing",
                    manifest_error="no frozen manifest at /w/cp/c0002|b/manifest.yaml",
                    citations={"cited_fraction": None},
                    abstract={"word_count": 55},
                    narration_count=4,
                    finding_count=None,
                ),
                # What `revision_lints` builds when `_draft_at` reports: every
                # metric nulled by `_unmeasured_lint`, since there is no text
                # to measure, and `manifest_error` left as the manifest's own
                # — which here is None, the frozen manifest having loaded.
                _revision(
                    tag="draft-x|02",
                    number=2,
                    cluster_id="c0003-c",
                    draft_status="unreadable",
                    draft_error=GATE_REFUSAL,
                    citations={"cited_fraction": None},
                    abstract={"word_count": None},
                    narration_count=None,
                    finding_count=None,
                ),
            ],
            "revisions_status": "read",
            "revisions_error": None,
            "build": {
                "exit_code": 1,
                "findings": ["stub abstract", "idnits warned"],
                "broken_references": ["RFC9999"],
                "idnits": {},
                "diagnostic_counts": {},
            },
        },
        "C1": {
            "revisions": [],
            "revisions_status": "unreadable",
            "revisions_error": YAML_REFUSAL,
            "build": None,
        },
    }
    for run_id, quality in qualities.items():
        run = copy.deepcopy(aggregate["runs"]["A1"])
        run["run_id"] = run_id
        run["quality"] = quality
        aggregate["runs"][run_id] = run
        aggregate["run_order"].append(run_id)
    return aggregate


def _section(text: str, heading: str) -> list[str]:
    """The lines under one ``##`` heading, up to the next one.

    Args:
        text: A rendered report.
        heading: The heading line, hashes included.

    Returns:
        The lines between that heading and the next ``## ``.
    """
    lines = text.splitlines()
    rest = lines[lines.index(heading) + 1 :]
    end = next((i for i, line in enumerate(rest) if line.startswith("## ")), len(rest))
    return rest[:end]


def _quality_tables(text: str) -> tuple[list[str], list[str]]:
    """The section's two tables, split where the second header starts.

    Both tables' first column is ``run``, so a row filtered by run id alone
    can come from either. Both headers open ``| run |`` and only they do.

    Args:
        text: A rendered report.

    Returns:
        The run-level rows and the revision-level rows, headers included.
    """
    rows = [line for line in _section(text, "## Quality") if line.startswith("|")]
    split = next(i for i, line in enumerate(rows[1:], 1) if line.startswith("| run |"))
    return rows[:split], rows[split:]


def test_the_quality_section_dashes_what_no_manifest_could_measure():
    """The payload's one rule, rendered: null is unmeasured and never zero.

    Both rows come from the same draft repository and differ only in whether
    their frozen manifest was there, so the columns that change across them
    are exactly the ones a manifest feeds. The text-only metrics stay real
    across that difference, which is the half a reader would lose if an
    unmeasured row were nulled wholesale.
    """
    section = _section(render_report(_quality_aggregate()), "## Quality")
    rows = [
        line
        for line in section
        if line.startswith(("| B1 | draft-x-00 |", "| B1 | draft-x-01 |"))
    ]

    assert rows == [
        "| B1 | draft-x-00 | 0 | cluster | read | read | 0.500 | 3 | 2 | 40 | — | — |",
        "| B1 | draft-x-01 | 1 | cluster | missing | read | — | — | 4 | 55 "
        "| no frozen manifest at /w/cp/c0002\\|b/manifest.yaml | — |",
    ]


def test_a_revision_map_that_would_not_load_has_no_revision_count():
    """Zero revisions and an unknown number of them must not render alike.

    ``revisions`` is ``[]`` in both cases, so a renderer taking its length
    would print ``0`` for a map nothing could enumerate. ``B1`` is in the same
    render to show the count is real when the map did load — without it this
    would pass against a renderer that dashed every count.
    """
    section = _section(render_report(_quality_aggregate()), "## Quality")

    assert [line for line in section if line.startswith("| B1 | 3 | read |")]
    (row,) = [line for line in section if line.startswith("| C1 |")]
    assert row.startswith("| C1 | — | unreadable |")
    assert row.endswith("| — | — | — |")


def test_a_build_that_ran_reports_its_counts_and_one_that_did_not_dashes_them():
    """The three build columns, on both sides of having been measured.

    ``B1`` was built and ``C1`` was not, in the same render. Without the
    measured side, a renderer that hard-coded the em dash for all three — or a
    ``_count`` that answered None for everything — would satisfy every other
    test here, since no other fixture in this module carries a build at all.

    ``exit_code`` is 1 on purpose: a zero would not tell a rendered exit code
    apart from a nulled one that had been read as a zero.
    """
    runs, _ = _quality_tables(render_report(_quality_aggregate()))

    assert [line for line in runs if line.startswith("| B1 |")] == [
        "| B1 | 3 | read | — | 1 | 2 | 1 |"
    ]
    assert [line for line in runs if line.startswith("| C1 |")][0].endswith(
        "| — | — | — |"
    )


def test_a_revision_with_no_draft_text_measures_nothing_and_stays_one_cell():
    """A tag the draft repository does not hold: every metric unmeasured.

    An absent revision must not read as an empty one, so this row is dashes
    where the rows above it carry numbers — the two are in the same render, so
    the dashes are this revision's condition and not what the table does to
    every row.

    It is also the third free-text column's escaping. ``draft_error`` embeds
    the ref twice, and a tag comes out of an agent-writable ``revisions.yaml``,
    so the pipes in it are content: unescaped they would buy the row two
    columns.
    """
    section = _section(render_report(_quality_aggregate()), "## Quality")
    (row,) = [line for line in section if line.startswith("| B1 | draft-x\\|02 |")]

    assert row == (
        "| B1 | draft-x\\|02 | 2 | cluster | read | unreadable | — | — | — | — | — "
        "| draft-x\\|02: could not list its tree: "
        "fatal: Not a valid object name draft-x\\|02 |"
    )
    # Twelve columns, so thirteen structural pipes.
    assert row.count("|") - row.count("\\|") == 13


def test_a_multi_line_map_error_cannot_add_rows_to_the_quality_tables():
    """PyYAML's message spans six lines; a table cell is one by construction.

    The count is over every non-blank line of the section and not over the
    lines that look like table rows: an error rendered raw breaks into
    continuation lines that start with a space, and counting rows alone would
    not see them.
    """
    section = _section(render_report(_quality_aggregate()), "## Quality")

    # Two tables: a header and a separator each, three run rows, and three
    # revision rows from the one run whose map enumerated.
    assert len([line for line in section if line.strip()]) == 10
    (row,) = [line for line in section if line.startswith("| C1 |")]
    assert "expected <block end>" in row
    # Seven columns, so eight structural pipes. The one inside the error is
    # content, and an unescaped one there would buy the row a column.
    assert row.count("|") - row.count("\\|") == 8


def test_a_run_analyzed_before_the_instrument_reports_nothing_measured():
    """D-30: ``_aggregate``'s run has no ``quality`` key and must still render.

    Dropping such a run from the section would be the same defect as printing
    zeros for it: a reader could not tell it from a run that was measured and
    scored nothing.
    """
    section = _section(render_report(_quality_aggregate()), "## Quality")

    assert [line for line in section if line.startswith("| A1 |")] == [
        "| A1 | — | — | — | — | — | — |"
    ]


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_no_line_ending_in_a_cell_can_add_a_row(breaker):
    """A table row is one line, so a value that ends a line ends the table.

    The newline test above is this one's first member. Neutralising ``\\n``
    alone is the enumeration that predicate escaping exists to retire: CR ends
    a row under CommonMark exactly as LF does, and five further characters end
    one under :meth:`str.splitlines`.
    """
    benign = render_report(_aggregate(run_id="r1r2"))
    hostile = render_report(_aggregate(run_id="r1" + breaker + "r2"))

    assert len(hostile.splitlines()) == len(benign.splitlines())
    row = next(line for line in hostile.splitlines() if line.startswith("| r1"))
    assert "r2 |" in row


@pytest.mark.parametrize("breaker", LINE_BREAKERS)
def test_no_line_ending_in_a_code_span_can_add_a_line(breaker):
    """``_code`` carries the same duty as ``cell``: one value, one line."""
    benign = render_report(_aggregate(target="r1r2"))
    hostile = render_report(_aggregate(target="r1" + breaker + "r2"))

    assert len(hostile.splitlines()) == len(benign.splitlines())
    target_line = next(
        line for line in hostile.splitlines() if line.startswith("- target:")
    )
    assert "r2`" in target_line
