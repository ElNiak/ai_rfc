"""The one spawn path: what both call sites disagreed about before it existed.

Each test here names one such disagreement. The whole-window launcher wrote the
MCP config and the guard settings itself and then never re-read the transcript
for cost; the per-cluster loop re-read the transcript but built its argv through
a second call into the launcher's private helpers. Where the two agreed only by
accident, that agreement is asserted here instead.

:func:`ai_rfc.driver.spawn.spawn` is monkeypatched rather than really launched:
its own lifetime guarantees are covered by ``test_spawn.py``, and what these
tests must observe is the argv, the environment and the transcript accounting
that :func:`run_session` builds around it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ai_rfc.driver import session
from ai_rfc.driver.arms import arm_profile


def _result(cost: float, session_id: str = "s1") -> dict[str, Any]:
    """One stream-json result event carrying a cost."""
    return {
        "type": "result",
        "subtype": "success",
        "session_id": session_id,
        "total_cost_usd": cost,
    }


def _fake_spawn(
    calls: list[dict[str, Any]],
    *,
    emits: tuple[dict[str, Any], ...] = (),
    exit_code: int | None = 0,
    timed_out: bool = False,
):
    """A stand-in for :func:`spawn` that records its call and writes events.

    It honours ``append`` exactly as the real one does. A stub that always
    truncated would hide a ``seen``-ignoring implementation, because the
    transcript would never hold the earlier sessions to double-count.
    """

    def fake(
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        events_path: Path,
        stderr_path: Path,
        timeout_s: int,
        append: bool = False,
    ) -> tuple[int | None, bool]:
        calls.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "env": dict(env),
                "events_path": events_path,
                "stderr_path": stderr_path,
                "timeout_s": timeout_s,
                "append": append,
            }
        )
        with open(events_path, "ab" if append else "wb") as handle:
            for event in emits:
                handle.write((json.dumps(event, sort_keys=True) + "\n").encode())
        return exit_code, timed_out

    return fake


def _spec(tmp_path: Path, **overrides: Any) -> Any:
    """A launchable spec; every test overrides only what it is about."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    prompt_file = tmp_path / "arm-A.md"
    prompt_file.write_text("the arm's system prompt\n")
    fields: dict[str, Any] = {
        "claude": "/opt/claude/bin/claude",
        "model": "opus",
        "effort": "high",
        "budget_usd": 5.0,
        "timeout_s": 600,
        "profile": tmp_path / "profile",
        "python": str(tmp_path / "venv" / "bin" / "python"),
        "workspace": workspace,
        "toolchain": tmp_path / "toolchain.json",
        "prompt_file": prompt_file,
        "task": "reconstruct cluster 7",
        "surface": arm_profile("A"),
    }
    fields.update(overrides)
    return session.SessionSpec(**fields)


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "A1"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _value_after(argv: list[str], flag: str) -> str:
    """The argument following ``flag``, so adjacency is asserted, not presence."""
    return argv[argv.index(flag) + 1]


def test_it_writes_the_mcp_config_and_the_guard_and_passes_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The side effects of ``prepare_argv`` are part of launching a session.

    The guard settings file is what mounts the ``PreToolUse`` hook; a launcher
    that built the argv without writing it would pass ``--settings`` at a file
    that does not exist, and the arm would run unconfined.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(session, "spawn", _fake_spawn(calls))
    run_dir = _run_dir(tmp_path)
    spec = _spec(tmp_path)

    session.run_session(spec, run_dir)

    mcp_path = run_dir / "ai_rfc.json"
    guard_path = run_dir / "guard.json"
    assert mcp_path.is_file()
    assert guard_path.is_file()
    argv = calls[0]["argv"]
    assert _value_after(argv, "--mcp-config") == str(mcp_path)
    assert _value_after(argv, "--settings") == str(guard_path)
    mounted = json.loads(guard_path.read_text())
    command = mounted["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert str(session.GUARD) in command
    assert session.GUARD.is_file()
    assert calls[0]["cwd"] == spec.workspace
    assert calls[0]["timeout_s"] == spec.timeout_s
    assert calls[0]["append"] is False


def test_the_budget_flag_carries_the_given_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller must be able to pass a remainder, not only a campaign's cap.

    The sweep gives each session what the run has left, so the run's total
    holds however many sessions it turns out to need; the one-shot
    consolidation passes the whole cap. Both go through this one flag.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(session, "spawn", _fake_spawn(calls))

    session.run_session(_spec(tmp_path, budget_usd=3.25), _run_dir(tmp_path))

    assert _value_after(calls[0]["argv"], "--max-budget-usd") == "3.25"


def test_the_cost_counts_only_result_events_at_or_after_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-read must not charge the run for every earlier session again.

    The transcript is shared: each session appends to it, so the events already
    in the file when this one starts are other sessions' and are already paid
    for. An implementation that summed the whole file would report 7.0 here.
    """
    run_dir = _run_dir(tmp_path)
    (run_dir / "events.jsonl").write_text(
        json.dumps(_result(1.0, "earlier-1"), sort_keys=True)
        + "\n"
        + json.dumps(_result(2.0, "earlier-2"), sort_keys=True)
        + "\n"
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        session, "spawn", _fake_spawn(calls, emits=(_result(4.0, "mine"),))
    )

    result = session.run_session(_spec(tmp_path, append=True), run_dir, seen=2)

    assert result.cost_usd == 4.0
    assert result.results_seen == 3
    assert calls[0]["append"] is True
    assert result.session_ids == ("earlier-1", "earlier-2", "mine")


def test_the_environment_is_closed_and_keeps_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is inherited beyond the contract, and ``USER`` is in it.

    Measured on Claude Code 2.1.247: drop ``USER`` and the CLI cannot reach its
    stored credentials, answering "Not logged in" however valid the profile.
    ``AI_RFC_TOOLCHAIN`` is the one conditional key — a session without a
    toolchain must not carry an empty one, which would name a path that is not
    there rather than none at all.
    """
    monkeypatch.setenv("AI_RFC_TEST_SENTINEL", "must-not-be-inherited")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(session, "spawn", _fake_spawn(calls))

    session.run_session(_spec(tmp_path), _run_dir(tmp_path))

    assert set(calls[0]["env"]) == {
        "CLAUDE_CONFIG_DIR",
        "AI_RFC_WORKSPACE",
        "AI_RFC_TOOLCHAIN",
        "PATH",
        "HOME",
        "USER",
        "LANG",
    }
    assert "AI_RFC_TEST_SENTINEL" not in calls[0]["env"]

    calls.clear()
    session.run_session(_spec(tmp_path, toolchain=None), _run_dir(tmp_path))

    assert "AI_RFC_TOOLCHAIN" not in calls[0]["env"]


def test_the_shim_directory_leads_the_path_when_the_spec_names_one(
    tmp_path: Path,
) -> None:
    """``bin_dir`` goes first, or the campaign's own shim is not what resolves.

    The campaign writes an ``ai_rfc`` shim pinning its frozen interpreter and
    puts that directory ahead of everything else. A venv that installed the
    console script of the same name would answer the arm too, so this is not
    the difference between a working arm and a broken one on the default
    configuration — but it is the difference between the two launchers writing
    the same ``env.json`` and writing different ones, and only the value, not
    the key, can show that. The key set is pinned above; nothing pinned the
    order until here.
    """
    venv_bin = Path(_spec(tmp_path).python).parent

    with_shim = session.session_env(_spec(tmp_path, bin_dir=tmp_path / "bin"))["PATH"]
    without = session.session_env(_spec(tmp_path))["PATH"]

    assert with_shim.startswith(f"{tmp_path / 'bin'}:")
    assert with_shim == f"{tmp_path / 'bin'}:{venv_bin}:/usr/bin:/bin"
    assert without == f"{venv_bin}:/usr/bin:/bin"


def test_a_spec_that_names_no_profile_falls_back_to_the_experiments_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A configuration that never named a profile must still launch.

    ``sessions.profile`` is declared with no default and the loader passes the
    raw ``None`` through, so the spec has to be able to carry one — and the
    environment it produces must name the directory the doctor reports rather
    than the string ``None``.
    """
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))

    assert session.resolve_profile(None) == tmp_path / "root" / "profile"

    env = session.session_env(_spec(tmp_path, profile=None))

    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "root" / "profile")
