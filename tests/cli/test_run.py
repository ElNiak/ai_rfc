"""`ai-rfc run`: perform what is next, then drive sessions or stop at the boundary."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from ai_rfc import cli
from ai_rfc.driver import DriverError
from ai_rfc.lifecycle.run import cli as run_cli
from ai_rfc.lifecycle.workspace import Layout

SESSIONS_BLOCK = "sessions:\n  budget_usd: 5\n"

#: A cluster bound whose id carries a newline and a whole second instruction.
#: The payload is the shape that matters: ``resume:`` is the prefix an operator
#: copies from, so a forged one is a fabricated command in the artifact the
#: real resume line lives in.
FORGED_BOUND = "cluster:c1\nresume: ai-rfc run --config /tmp/evil.yaml"

#: The same payload in ``--retry``'s spelling. A cluster id reaches the same
#: artifacts from either flag, so it needs the same two ends.
FORGED_RETRY = "c1\nresume: ai-rfc run --config /tmp/evil.yaml"


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
    assert calls[0]["retry"] is None
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
    # The refusal interpolates the operator's bound, so it is itself a
    # line-per-record artifact. Asserting substrings alone passes just as
    # happily when the bound has forged a second line beneath them.
    assert len(err.splitlines()) == 1


@pytest.mark.parametrize(
    "bound",
    [
        "forge",
        "mining",
        "ordinal:x",
        "cluster:",
        # `int` accepts surrounding whitespace, so an ordinal that merely
        # "survives int()" survives a newline too — and the bound is echoed
        # back on a report line. Without these two the guard is `int(raw)` in
        # everything but spelling.
        "ordinal:7\n",
        "ordinal: 7",
    ],
)
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


@pytest.mark.parametrize(
    "bound",
    [FORGED_BOUND, "cluster:c1\x07", "cluster:c1 evil", "cluster:c1‮"],
)
def test_a_cluster_bound_that_could_forge_a_line_is_refused_by_the_parser(
    initialised, bound
):
    """The first of the two ends: refuse it, as ``ordinal:`` already did.

    A cluster id cannot be checked for *membership* here — that needs a
    timeline, which the sweep holds and a parser does not — so the guard at
    this end is the character class, exactly as
    :func:`ai_rfc.driver.printable` defines it: ``str.isprintable`` is
    False for every Cc, Cf, Cs, Co and Cn and for every separator but the
    plain space. The four cases are four different ways past a hand-written
    list: a newline, a C0 bell, LINE SEPARATOR (which ``str.splitlines``
    already treats as a break) and RIGHT-TO-LEFT OVERRIDE.
    """
    config_path, _ = initialised

    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--config", str(config_path), "--until", bound])

    assert raised.value.code == 2


def test_the_refusal_cannot_be_forged_by_a_bound_that_bypassed_the_parser(
    initialised, capsys
):
    """The second end, pinned on its own so the parser fix cannot mask it.

    ``run_stages`` is reachable without ``_bound`` — from
    :func:`ai_rfc.lifecycle.run.cli.run` given any namespace, and from any
    future caller that builds one. A guard that lives only in the parser is a
    rule callers follow rather than a boundary, which is the distinction
    :func:`ai_rfc.driver.sweep.report` was written around. So the message
    interpolates the bound through ``repr`` and the stream escapes it again.
    """
    config_path, _ = initialised

    code = run_cli.run(
        argparse.Namespace(config=config_path, until=FORGED_BOUND, retry=None)
    )

    assert code == 1
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    # The payload is still *visible*, quoted inside the message — that is the
    # point of `repr` rather than deletion: the operator sees what they typed.
    # What must not exist is a **line** that an operator's eye, or a log
    # reader splitting on newlines, takes for a record of its own.
    assert not any(line.startswith("resume:") for line in err.splitlines())
    assert "\\n" in err  # the newline survives as an escape, not as a break


def test_the_refusal_quotes_the_bound_in_its_own_message(initialised):
    """The repr, pinned where the stderr boundary cannot stand in for it.

    Both layers make the printed line safe, so a test that reads stderr passes
    with either one alone — the two mask each other, which is the same defect
    shape as an unpinned guard. Asserted on ``str(error)`` instead: the
    exception's own text, before any stream has touched it.

    It matters on its own because the message travels further than the
    terminal — a caller that logs it, or wraps it in a JSON field, gets the
    exception, not what ``report`` printed.
    """
    from ai_rfc.lifecycle import LifecycleError

    config_path, _ = initialised

    with pytest.raises(LifecycleError) as raised:
        run_cli.run_stages(config_path, until=FORGED_BOUND)

    assert len(str(raised.value).splitlines()) == 1
    assert "\\n" in str(raised.value)


def test_the_parser_and_the_sweep_accept_the_same_stage_bounds():
    """One grammar, two enforcement points, and they must not drift apart.

    ``--until``'s stage spelling is a closed set in **two** places: the parser
    offers ``_walkable()`` and ``sweep._bound_reached`` matches
    ``SWEPT_STAGES``. They agreed by coincidence until the sweep accepted every
    name in ``obs.stages`` — nine more — and an API caller could then get a
    bound that resolved and a resume line the root parser refuses.

    Asserted as equality of the two lists rather than of their contents, since
    order is what ``--until``'s help text prints.
    """
    from ai_rfc.driver.sweep import SWEPT_STAGES

    assert run_cli._walkable() == list(SWEPT_STAGES)


# --- --retry: the line cluster_halted tells the operator to type ------------


def test_a_retry_is_handed_to_the_sweep(initialised, monkeypatch):
    """``cluster_halted``'s resume line was uncopyable until this argument existed.

    ``sweep.run`` has accepted ``retry=`` since Task 9 and nothing passed it,
    while ``stop.resume_line`` has printed ``ai-rfc run --config <path>
    --retry <id>`` for as long. Task 11 is what made that stop reachable from
    ``ai-rfc run`` at all — so the one line D59 promises the operator could be
    reached, copied, and then refused by ``run``'s own parser.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["run", "--config", str(config_path), "--retry", "c1"]) == 0

    assert len(calls) == 1
    assert calls[0]["retry"] == "c1"


def test_a_retry_that_names_no_cluster_is_refused_by_the_parser(initialised, capsys):
    """An empty id forgives nothing and would be indistinguishable from none.

    ``record.attempts`` refuses it downstream for a sharper reason — an empty
    id matches the rows that deliberately name no cluster, such as a
    consolidation's — but that refusal arrives as a ``DriverError`` after the
    workspace has already been read.

    ``unrecognized`` is asserted absent because argparse answers an *unknown*
    flag with the same exit 2. Without that line this test passes against a
    ``run`` that has no ``--retry`` at all, which is the state it was written
    against — a refusal for the wrong reason reads exactly like a refusal.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--config", str(config_path), "--retry", ""])

    assert raised.value.code == 2
    assert "unrecognized" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "retry",
    [
        FORGED_RETRY,  # forges a second record
        "c1\x07",  # a C0 bell
        "c1 ",  # LINE SEPARATOR, a break by str.splitlines' definition
        "c1‮",  # RIGHT-TO-LEFT OVERRIDE, reorders without breaking
    ],
)
def test_a_retry_that_could_forge_a_line_is_refused_by_the_parser(
    initialised, capsys, retry
):
    """The first of ``--retry``'s two ends, and the same four escape classes.

    Membership is the real guard on a cluster id and it lives in
    ``sweep.observe``, which has the timeline. What is left at the parser is
    the character class — and it is needed, because the value is interpolated
    into the sessionless refusal below, which is a line an operator copies
    from. Refused rather than escaped: at the parser there is still somewhere
    to say no.

    ``unrecognized`` absent for the reason the empty-id test gives: argparse's
    answer to an unknown flag is also exit 2.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    with pytest.raises(SystemExit) as raised:
        cli.main(["run", "--config", str(config_path), "--retry", retry])

    assert raised.value.code == 2
    assert "unrecognized" not in capsys.readouterr().err


def test_a_retry_without_sessions_is_refused(initialised, capsys):
    """D16's rule in its third setting: a flag that cannot fire is refused.

    Only the sweep forgives attempts, and only a configuration with sessions
    reaches the sweep. Left unrefused, ``run --retry c1`` on a hand-mined
    workspace walks to the boundary, reports success and forgives nothing —
    the operator watches the same halt repeat, which is precisely what
    ``observe``'s own membership refusal exists to prevent.
    """
    config_path, root = initialised

    assert cli.main(["run", "--config", str(config_path), "--retry", "c1"]) == 1

    assert not Layout(root).commits.exists()
    err = capsys.readouterr().err
    assert "c1" in err and "sessions" in err
    assert len(err.splitlines()) == 1


def test_the_retry_refusal_cannot_be_forged_by_a_value_that_bypassed_the_parser(
    initialised, capsys
):
    """The second end, pinned on its own so the parser fix cannot mask it.

    ``run_stages`` is reachable without the parser — from
    :func:`ai_rfc.lifecycle.run.cli.run` given any namespace — so a guard that
    lives only in ``configure`` is a rule callers follow rather than a
    boundary. The payload stays visible, quoted inside the one line; what must
    not exist is a **line** a reader takes for a record of its own.
    """
    config_path, _ = initialised

    code = run_cli.run(
        argparse.Namespace(config=config_path, until=None, retry=FORGED_RETRY)
    )

    assert code == 1
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    assert not any(line.startswith("resume:") for line in err.splitlines())
    assert "\\n" in err


def test_the_retry_refusal_quotes_the_value_in_its_own_message(initialised):
    """The repr, pinned where the stderr boundary cannot stand in for it.

    Both layers make the printed line safe, so a test reading stderr passes
    with either alone and the two mask each other. Asserted on ``str(error)``
    instead — the exception's own text, before any stream touches it, which is
    what a caller that logs it or wraps it in a JSON field receives.
    """
    from ai_rfc.lifecycle import LifecycleError

    config_path, _ = initialised

    with pytest.raises(LifecycleError) as raised:
        run_cli.run_stages(config_path, retry=FORGED_RETRY)

    assert len(str(raised.value).splitlines()) == 1
    assert "\\n" in str(raised.value)


def test_a_yaml_syntax_error_keeps_the_parser_s_own_lines(initialised, capsys):
    """PyYAML's diagnostic is a block, and its caret line points at a column.

    ``config.py`` wraps ``yaml.YAMLError`` verbatim, so the text that reaches
    the operator is the parser's own multi-line report: the problem, the
    context, the offending line and a ``^`` under the character. Collapsed to
    one line, the caret points at nothing — it is the one part of the message
    whose meaning is purely positional.

    Asserted as structure, not substrings: more than one line, and a line that
    is *only* whitespace and a caret.
    """
    config_path, _ = initialised
    config_path.write_text("name: fixture\n  bad: [unclosed\n")

    assert cli.main(["run", "--config", str(config_path)]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) > 1
    assert any(line.strip() == "^" for line in lines)


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
