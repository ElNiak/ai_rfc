from ai_rfc.experiment.report import render_report


def _aggregate():
    cluster = {
        "cluster_id": "c0002-x",
        "ordinal": 2,
        "completed": True,
        "artifacts": True,
    }
    run = {
        "run_id": "A1",
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
        "pass_k": {"c0002-x": True},
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
        "target": "fixture",
        "window": [2, 2],
        "model": "m",
        "effort": "high",
        "claude_version": "fake-claude 0.0.0",
        "git": {"panther": "abc", "ai_rfc": "def"},
        "parity_pre_run": {"passed": True, "summary": "ok"},
        "run_order": ["A1"],
        "runs": {"A1": run},
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
    assert "fake-claude 0.0.0" in text and "abc" in text


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
