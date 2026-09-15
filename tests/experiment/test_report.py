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
    """``_code`` carries the same duty as ``_cell``: one value, one line."""
    benign = render_report(_aggregate(target="r1r2"))
    hostile = render_report(_aggregate(target="r1" + breaker + "r2"))

    assert len(hostile.splitlines()) == len(benign.splitlines())
    target_line = next(
        line for line in hostile.splitlines() if line.startswith("- target:")
    )
    assert "r2`" in target_line
