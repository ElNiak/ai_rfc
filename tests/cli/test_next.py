"""``ai-rfc next``: perform exactly one action, then report what it did.

``next`` is ``run``'s one-step form, so most of what is worth asserting here
is that the two do not *diverge*: the same bound grammar, the same
``--retry``, the same handover to the same sweep with ``mode="one"``. A second
parser for one grammar is how they drift, and a second refusal for one
boundary is how they disagree about what a workspace without sessions means.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

import pytest

from ai_rfc import cli
from ai_rfc.driver import DriverError
from ai_rfc.entrypoints import ENTRY_POINTS
from ai_rfc.lifecycle.next import cli as next_cli
from ai_rfc.lifecycle.workspace import Layout

SESSIONS_BLOCK = "sessions:\n  budget_usd: 5\n"

#: A ``--retry`` carrying a newline and a whole second instruction. ``resume:``
#: is the prefix an operator copies from, so a forged one is a fabricated
#: command in the artifact the real resume line lives in.
FORGED_RETRY = "c1\nresume: ai-rfc run --config /tmp/evil.yaml"


def _record_sweep(
    monkeypatch: pytest.MonkeyPatch, *, code: int = 0
) -> list[dict[str, Any]]:
    """Stand in for :func:`ai_rfc.driver.sweep.run` and record its arguments.

    What ``next`` owes the sweep is the handover. What the sweep then does
    with it — one action, a status record, a ledger and a resume line — is
    ``tests/driver/test_sweep.py``'s, so this records rather than simulates.
    """
    calls: list[dict[str, Any]] = []

    def _run(cfg: Any, workspace: Path, **kwargs: Any) -> int:
        calls.append({"cfg": cfg, "workspace": workspace, **kwargs})
        return code

    monkeypatch.setattr(next_cli.sweep, "run", _run)
    return calls


# --- the verb itself --------------------------------------------------------


def test_next_is_registered_immediately_after_run():
    """Declaration order is help order, and ``next`` is ``run``'s one-step form.

    ``test_entries_sharing_a_section_are_contiguous`` keeps the ``Lifecycle``
    block together but says nothing about the order inside it, so a reader of
    ``ai-rfc --help`` could meet ``next`` three rows away from the verb it
    steps through. Written as an index rather than a whole-tuple golden: the
    adjacency is the property, not the rest of the table.
    """
    verbs = [entry.verb for entry in ENTRY_POINTS]

    assert verbs[verbs.index("run") + 1] == "next"


def test_next_asks_the_sweep_for_exactly_one_action(initialised, monkeypatch):
    """The whole verb: ``sweep.run`` with ``mode="one"``, nothing else.

    Every argument of the handover is asserted separately, because each is an
    independent way to be wrong — the sealed copy instead of the operator's
    configuration, the workspace's own ``recon.yaml`` instead of the path they
    typed (which is what the resume line prints back), a dropped bound, or
    ``mode`` left at its ``"all"`` default, which would sweep the whole window
    from a verb that promises one action.
    """
    config_path, root = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["next", "--config", str(config_path)]) == 0

    assert len(calls) == 1
    assert calls[0]["mode"] == "one"
    assert calls[0]["workspace"] == root
    assert calls[0]["cfg"].sessions.budget_usd == 5.0
    assert calls[0]["until"] is None
    assert calls[0]["retry"] is None
    assert calls[0]["config_path"] == config_path


def test_the_sweeps_exit_code_is_nexts_exit_code(initialised, monkeypatch):
    """3 is ``check --strict`` finding something; ``next`` must not flatten it.

    ``next`` reaches the build gate whenever its one action *is* the gate —
    the last row of spec §5's table — so this is a code it can really return.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    _record_sweep(monkeypatch, code=3)

    assert cli.main(["next", "--config", str(config_path)]) == 3


def test_next_does_not_walk_the_deterministic_stages_itself(initialised, monkeypatch):
    """``run``'s loop is not copied here, and must not be.

    The sweep performs ``history``, ``timeline`` and ``views`` itself — they
    are row 2 of the same state machine, and ``SWEPT_STAGES`` is exactly
    ``run``'s ``_walkable()``. A ``next`` that walked them first would perform
    *three* actions before asking the sweep for one.
    """
    config_path, root = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    _record_sweep(monkeypatch)

    assert cli.main(["next", "--config", str(config_path)]) == 0

    assert not Layout(root).commits.exists()


# --- the boundary: a workspace with no sessions ------------------------------


def test_next_without_sessions_is_refused_rather_than_reaching_the_sweep(
    initialised, capsys, monkeypatch
):
    """``next`` performs one row of the *sweep's* table; without one there is none.

    The asymmetry with ``run`` is deliberate and is the reason this refusal
    lives here rather than being inherited. ``run`` without sessions has real
    work to do — the deterministic stages — and stops at the agent boundary
    reporting 0, so a hand-mined workspace stays possible. ``next`` has
    nothing it could perform, and ``sweep.run``'s own refusal ("no sessions
    are configured") would reach the operator as a broken verb rather than as
    the deliberate boundary it is. So the message names ``run`` as the way to
    perform the deterministic half.
    """
    config_path, root = initialised
    calls = _record_sweep(monkeypatch)

    assert cli.main(["next", "--config", str(config_path)]) == 1

    assert calls == []
    assert not Layout(root).commits.exists()
    err = capsys.readouterr().err
    assert "sessions" in err
    assert f"ai-rfc run --config {config_path}" in err
    assert len(err.splitlines()) == 1


def test_the_sessionless_refusal_prints_a_line_the_root_parser_accepts(
    initialised, capsys
):
    """The one line printed to unblock an operator must actually run.

    A path holding a space is not adversarial — it is a Documents folder — and
    unquoted it splits into two argv words, so the root parser answers the
    instruction it was handed with ``unrecognized arguments: con.yaml`` and
    exit 2. ``stop._quoted`` has guarded this exact value inside
    ``resume_line`` since Task 9; this line is the eighth place in the row
    where the same shape appeared, and the first outside the driver.

    Fed back as **argv** rather than matched as a substring: a substring
    assertion passes on a line that cannot be run, which is the whole defect.
    The config file is copied to a spaced name rather than the workspace being
    rebuilt at one, because what the line interpolates is the path the
    operator typed.
    """
    config_path, _ = initialised
    spaced = config_path.parent / "re con.yaml"
    spaced.write_text(config_path.read_text())

    assert cli.main(["next", "--config", str(spaced)]) == 1

    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1
    instruction = err.split("with: ", 1)[1].strip()
    argv = shlex.split(instruction)
    assert argv[0] == "ai-rfc"
    parsed = cli.build_parser().parse_args(argv[1:])
    assert parsed.verb == "run"
    assert parsed.config == spaced


# --- --until and --retry: one grammar, shared with run ----------------------


@pytest.mark.parametrize("bound", ["history", "cluster:c1", "ordinal:2"])
def test_a_bound_is_handed_to_the_sweep(initialised, monkeypatch, bound):
    """All three of spec §5's spellings bound ``next`` as they bound ``run``.

    A stage bound is resolved by the sweep here rather than by a walk of
    ``next``'s own, which is why ``history`` is in this list and not in a
    separate test: ``next`` has no walk.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["next", "--config", str(config_path), "--until", bound]) == 0

    assert len(calls) == 1
    assert calls[0]["until"] == bound


@pytest.mark.parametrize(
    "bound",
    [
        "forge",
        "mining",
        "ordinal:x",
        "cluster:",
        "ordinal:7\n",
        "ordinal: 7",
        "cluster:c1\nresume: ai-rfc run --config /tmp/evil.yaml",
    ],
)
def test_a_bound_that_names_nothing_is_refused_by_nexts_parser_too(
    initialised, capsys, bound
):
    """The point of reusing ``run``'s validator, stated as a test.

    Every case here is one ``run``'s parser already refuses. A second,
    hand-written grammar for ``next`` would pass some of them — the whitespace
    ordinal and the forged cluster id are exactly the two that took a fix
    round to get right the first time — and the two verbs would then disagree
    about what ``--until`` means while both claiming spec §5.

    ``unrecognized`` is asserted absent because argparse answers an *unknown*
    flag with the same exit 2: without it, a ``next`` that never declared
    ``--until`` would pass every case here.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    with pytest.raises(SystemExit) as raised:
        cli.main(["next", "--config", str(config_path), "--until", bound])

    assert raised.value.code == 2
    assert "unrecognized" not in capsys.readouterr().err


def test_a_retry_is_handed_to_the_sweep(initialised, monkeypatch):
    """``next`` forgives a halted cluster's attempts exactly as ``run`` does."""
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)
    calls = _record_sweep(monkeypatch)

    assert cli.main(["next", "--config", str(config_path), "--retry", "c1"]) == 0

    assert len(calls) == 1
    assert calls[0]["retry"] == "c1"


@pytest.mark.parametrize("retry", ["", FORGED_RETRY])
def test_a_retry_that_names_nothing_or_forges_a_line_is_refused(
    initialised, capsys, retry
):
    """The same two refusals ``run``'s ``--retry`` makes, from the same validator."""
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    with pytest.raises(SystemExit) as raised:
        cli.main(["next", "--config", str(config_path), "--retry", retry])

    assert raised.value.code == 2
    assert "unrecognized" not in capsys.readouterr().err


# --- refusals reach the operator as a line, not a traceback ------------------


def test_a_driver_error_is_reported_rather_than_raised(
    initialised, capsys, monkeypatch
):
    """``DriverError`` is a ``RuntimeError``, so a ``LifecycleError`` tuple misses it.

    The sweep raises one for a bound or a ``--retry`` naming no cluster of
    this timeline, and it does so in ``observe``, before anything is launched.
    ``run`` had the same gap until Task 11; copying its ``except`` tuple is
    what closes it here, and nothing else would.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    def _refuse(*_args: Any, **_kwargs: Any) -> int:
        raise DriverError("cluster id 'c9' is not one of this workspace's clusters")

    monkeypatch.setattr(next_cli.sweep, "run", _refuse)

    assert cli.main(["next", "--config", str(config_path), "--retry", "c9"]) == 1
    assert "not one of this workspace" in capsys.readouterr().err


def test_a_forged_retry_that_bypassed_the_parser_is_one_line_from_the_sweep(
    initialised, capsys
):
    """``--retry``'s second end, reached with the parser stepped over.

    ``run_one`` is reachable without ``configure`` — from
    :func:`ai_rfc.lifecycle.next.cli.run` given any namespace — so the parser
    guard alone is a rule callers follow rather than a boundary. The sink here
    is not ``next``'s: it is ``sweep.observe``'s membership check, whose
    message quotes the id, and ``lifecycle.common.report``, which escapes it.
    Asserted end to end rather than mocked, because what is being pinned is
    that the two really do compose on this path.
    """
    config_path, _ = initialised
    config_path.write_text(config_path.read_text() + SESSIONS_BLOCK)

    code = next_cli.run(
        argparse.Namespace(config=config_path, until=None, retry=FORGED_RETRY)
    )

    assert code == 1
    lines = capsys.readouterr().err.splitlines()
    # The drift notes are the sessions: block this test just appended, which
    # `init` did not seal — one line per field, and not what is under test.
    refusals = [line for line in lines if not line.startswith("note: config drift:")]
    assert len(refusals) == 1
    assert refusals[0].startswith("error: cluster id ")
    assert not any(line.startswith("resume:") for line in lines)
    assert "\\n" in refusals[0]  # a newline survives as an escape, not a break


def test_next_needs_an_initialised_workspace(tmp_path, source_repo, capsys):
    """Row 1 of the table is ``needs_init``, but the workspace must exist first."""
    config_path = tmp_path / "recon.yaml"
    config_path.write_text(
        f"name: fixture\nworkspace: {tmp_path / 'nope'}\nsource:\n"
        f"  repo: {source_repo}\n  host: none\n  pin: main\n"
        "draft:\n  name: draft-test-fixture\n" + SESSIONS_BLOCK
    )

    assert cli.main(["next", "--config", str(config_path)]) == 1
    assert "ai-rfc init" in capsys.readouterr().err
