"""`ai-rfc run`: perform what is next, then drive sessions or stop at the boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_rfc import cli
from ai_rfc.driver import DriverError
from ai_rfc.lifecycle.run import cli as run_cli
from ai_rfc.lifecycle.workspace import Layout

SESSIONS_BLOCK = "sessions:\n  budget_usd: 5\n"


def _record_sweep(
    monkeypatch: pytest.MonkeyPatch, *, code: int = 0
) -> list[dict[str, Any]]:
    """Stand in for :func:`ai_rfc.driver.sweep.run` and record its arguments.

    What ``run`` owes the sweep is the handover — the configuration, the
    workspace, the bound and the path a resume line names. What the sweep then
    does with them is ``tests/driver/test_sweep.py``'s, so this records rather
    than simulates.
    """
    calls: list[dict[str, Any]] = []

    def _run(cfg: Any, workspace: Path, **kwargs: Any) -> int:
        calls.append({"cfg": cfg, "workspace": workspace, **kwargs})
        return code

    monkeypatch.setattr(run_cli.sweep, "run", _run)
    return calls


def test_run_performs_the_deterministic_stages_and_stops_at_mining(initialised, capsys):
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path)]) == 0
    ws = Layout(root)
    assert ws.commits.exists() and ws.timeline_json.exists()
    assert any((ws.clusters).glob("*/view.json"))
    err = capsys.readouterr().err
    assert "history" in err and "timeline" in err and "views" in err
    assert "boundary: mining" in err
    assert "clusters: 0 of" in err and "done" in err
    assert "sessions: not configured" in err


def test_a_second_run_performs_nothing_new(initialised, capsys):
    config_path, _ = initialised
    cli.main(["run", "--config", str(config_path)])
    capsys.readouterr()
    assert cli.main(["run", "--config", str(config_path)]) == 0
    err = capsys.readouterr().err
    assert "performed: nothing" in err and "boundary: mining" in err


def test_until_stops_after_the_named_stage(initialised, capsys):
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    ws = Layout(root)
    assert ws.commits.exists() and not ws.timeline_json.exists()
    assert "stopped after history" in capsys.readouterr().err


def test_until_stops_again_when_the_named_stage_is_already_current(initialised, capsys):
    """``--until`` bounds the walk, not only the stages this invocation performed.

    The first run performs ``history`` and stops. The second finds it already
    done, and must still stop there rather than stepping over the bound and
    performing ``timeline`` and ``views`` — a documented flag overrunning on
    its second invocation.
    """
    config_path, root = initialised
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    capsys.readouterr()
    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0
    err = capsys.readouterr().err
    assert "stopped after history" in err
    assert not Layout(root).timeline_json.exists()


def test_a_drifted_pin_is_refused_before_anything_runs(initialised, capsys):
    config_path, root = initialised
    config_path.write_text(config_path.read_text().replace("pin: main", "pin: v9"))
    assert cli.main(["run", "--config", str(config_path)]) == 1
    err = capsys.readouterr().err
    assert "refused" in err and "source.pin" in err
    assert not Layout(root).commits.exists()


def test_a_noted_drift_is_reported_and_run_continues(initialised, capsys, monkeypatch):
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)
    assert cli.main(["run", "--config", str(config_path)]) == 0
    err = capsys.readouterr().err
    assert "note: config drift" in err and "sessions.budget_usd" in err
    assert len(calls) == 1


# --- the boundary, and what is on the far side of it ------------------------


def test_a_configured_sessions_block_drives_the_sweep(initialised, capsys, monkeypatch):
    """``run`` no longer stops at ``mining`` when it has sessions to drive.

    Every argument of the handover is pinned, because each one is a separate
    way to be wrong: the sealed copy instead of the operator's configuration
    (a ``sessions:`` block added after ``init`` is *noted* drift, so the seal
    does not carry it), the workspace's own ``recon.yaml`` instead of the path
    the operator typed (which is what the sweep's resume line prints), or the
    bound dropped on the floor.
    """
    config_path, root = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["run", "--config", str(config_path)]) == 0

    assert len(calls) == 1
    assert calls[0]["workspace"] == root
    assert calls[0]["cfg"].sessions.budget_usd == 5.0
    assert calls[0]["until"] is None
    assert calls[0]["config_path"] == config_path
    err = capsys.readouterr().err
    assert "boundary: mining" not in err
    assert "sessions: configured" not in err


def test_the_sweeps_exit_code_is_runs_exit_code(initialised, monkeypatch):
    """3 is ``check --strict`` finding something; ``run`` must not flatten it."""
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    _record_sweep(monkeypatch, code=3)

    assert cli.main(["run", "--config", str(config_path)]) == 3


def test_without_sessions_the_boundary_still_stops_the_walk(
    initialised, capsys, monkeypatch
):
    """A hand-mined workspace stays possible — the spec settles this.

    Not merely "the instruction printed": the sweep must not be reached at
    all, since it refuses a configuration without sessions and that refusal
    would read as a broken ``run`` rather than as a deliberate boundary.
    """
    config_path, _ = initialised
    calls = _record_sweep(monkeypatch)

    assert cli.main(["run", "--config", str(config_path)]) == 0

    assert calls == []
    err = capsys.readouterr().err
    assert "boundary: mining" in err
    assert (
        "sessions: not configured (add a sessions: block to let ai-rfc run "
        "drive model sessions)" in err
    )


def test_a_stage_bound_stops_before_the_sweep_is_reached(
    initialised, capsys, monkeypatch
):
    """``--until views`` bounds the walk, sessions configured or not."""
    config_path, root = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["run", "--config", str(config_path), "--until", "history"]) == 0

    assert calls == []
    assert not Layout(root).timeline_json.exists()
    assert "stopped after history" in capsys.readouterr().err


# --- --until: spec §5's three spellings -------------------------------------


@pytest.mark.parametrize("bound", ["cluster:c1", "ordinal:2"])
def test_a_cluster_or_ordinal_bound_is_handed_to_the_sweep(
    initialised, monkeypatch, bound
):
    """Spec §5 gives ``--until`` three spellings; CLI-1 wired only stages.

    The two positional spellings mean nothing to the deterministic walk — no
    stage is named ``cluster:c1`` — so they pass through it untouched and are
    resolved against the timeline by the sweep, which is the only layer that
    has one.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["run", "--config", str(config_path), "--until", bound]) == 0

    assert len(calls) == 1
    assert calls[0]["until"] == bound


def test_a_cluster_bound_without_sessions_is_refused(initialised, capsys):
    """A bound nothing can honour must be refused, not silently overrun.

    This is D16's rule in its second setting. ``--until forge`` was kept out
    of the stage choices because the walk skips ``forge``, so the bound could
    never fire; a cluster bound on a workspace with no ``sessions:`` block is
    the same shape — only the sweep resolves cluster bounds, and without
    sessions there is no sweep.
    """
    config_path, root = initialised

    assert cli.main(["run", "--config", str(config_path), "--until", "cluster:c1"]) == 1

    assert not Layout(root).commits.exists()
    err = capsys.readouterr().err
    assert "cluster:c1" in err and "sessions" in err


@pytest.mark.parametrize("bound", ["forge", "mining", "ordinal:x", "cluster:"])
def test_a_bound_that_names_nothing_is_refused_by_the_parser(initialised, bound):
    """The syntax is a closed set of three spellings, checked before any work.

    ``forge`` and ``mining`` are the two stages a walkable-stage bound must
    keep refusing — ``forge`` is skipped as optional and ``mining`` is the
    boundary itself, so neither can ever match.
    """
    config_path, _ = initialised

    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--config", str(config_path), "--until", bound])

    assert raised.value.code == 2


def test_a_bound_the_sweep_refuses_is_reported_rather_than_raised(
    initialised, capsys, monkeypatch
):
    """``DriverError`` is not a ``LifecycleError``; ``run`` caught neither.

    The sweep resolves a cluster bound against the real timeline and raises
    on one that names nothing. Reported here rather than simulated: the bound
    is checked before any session launches, so nothing is spawned.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    def _refuse(*_args: Any, **_kwargs: Any) -> int:
        raise DriverError("'c9' is not one of this workspace's clusters")

    monkeypatch.setattr(run_cli.sweep, "run", _refuse)

    assert cli.main(["run", "--config", str(config_path), "--until", "cluster:c9"]) == 1
    assert "not one of this workspace" in capsys.readouterr().err


def test_run_needs_an_initialised_workspace(tmp_path, source_repo, capsys):
    config_path = tmp_path / "recon.yaml"
    config_path.write_text(
        f"name: fixture\nworkspace: {tmp_path / 'nope'}\nsource:\n"
        f"  repo: {source_repo}\n  host: none\n  pin: main\n"
        "draft:\n  name: draft-test-fixture\n"
    )
    assert cli.main(["run", "--config", str(config_path)]) == 1
    assert "ai-rfc init" in capsys.readouterr().err
