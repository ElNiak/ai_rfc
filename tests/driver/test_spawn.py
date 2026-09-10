import signal
import subprocess

import pytest

from ai_rfc.driver import spawn as spawn_module


def test_an_interrupt_kills_the_group_it_started(tmp_path, monkeypatch):
    """Ctrl-C must reach the session, not just the process that launched it.

    ``start_new_session=True`` puts the child in its own process group, so the
    terminal's SIGINT never reaches it. Without an explicit group kill the
    session and its MCP server outlive the driver and keep spending.
    """
    killed: list[tuple[int, int]] = []

    class _Interrupting:
        """Interrupt the first wait, then reap cleanly on the group-kill wait.

        Raising on every call would make the cleanup's own
        ``process.wait(timeout=KILL_GRACE_S)`` raise inside the handler, so the
        implementer would never see the full SIGTERM -> grace -> reap path run.
        """

        pid = 4242
        calls = 0
        returncode = None

        def wait(self, timeout=None):
            type(self).calls += 1
            if type(self).calls == 1:
                raise KeyboardInterrupt
            return 0

    monkeypatch.setattr(
        spawn_module.subprocess, "Popen", lambda *a, **k: _Interrupting()
    )
    monkeypatch.setattr(
        spawn_module.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    with pytest.raises(KeyboardInterrupt):
        spawn_module.spawn(
            ["true"],
            cwd=tmp_path,
            env={},
            events_path=tmp_path / "events.jsonl",
            stderr_path=tmp_path / "stderr.log",
            timeout_s=30,
        )

    assert killed and killed[0] == (4242, signal.SIGTERM)


def _spawn(module, tmp_path):
    """Call :func:`spawn` with the arguments every test here shares."""
    return module.spawn(
        ["true"],
        cwd=tmp_path,
        env={},
        events_path=tmp_path / "events.jsonl",
        stderr_path=tmp_path / "stderr.log",
        timeout_s=30,
    )


def test_a_second_interrupt_still_escalates_to_sigkill(tmp_path, monkeypatch):
    """A double-tap must not strand a child that is ignoring SIGTERM.

    The two conditions are correlated, not independent: a healthy session exits
    in under a second, so a second Ctrl-C can only land inside the grace window
    when the child is holding it open -- which is exactly the case where SIGKILL
    is the only thing that ends the spending.
    """
    killed: list[tuple[int, int]] = []

    class _DoubleTapped:
        """Interrupt the main wait and the grace wait, then reap on the third."""

        pid = 4242
        calls = 0
        returncode = None

        def wait(self, timeout=None):
            type(self).calls += 1
            if type(self).calls <= 2:
                raise KeyboardInterrupt
            return 0

    monkeypatch.setattr(
        spawn_module.subprocess, "Popen", lambda *a, **k: _DoubleTapped()
    )
    monkeypatch.setattr(
        spawn_module.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    with pytest.raises(KeyboardInterrupt):
        _spawn(spawn_module, tmp_path)

    assert killed == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_an_interrupt_after_a_clean_exit_does_not_signal_a_dead_group(
    tmp_path, monkeypatch
):
    """An interrupt can land after the child was already reaped.

    ``KeyboardInterrupt`` is asynchronous, so it may arrive between ``wait``
    returning and the ``try`` body ending. Signalling then reaches a pid that no
    longer exists and the ``ProcessLookupError`` replaces the operator's
    interrupt with a crash. The timeout path cannot hit this: ``TimeoutExpired``
    is itself proof the child is still alive.
    """
    killed: list[tuple[int, int]] = []

    class _ReapedThenInterrupted:
        """Reap the child, then interrupt as the real signal would."""

        pid = 4242
        returncode = None

        def wait(self, timeout=None):
            type(self).returncode = 0
            raise KeyboardInterrupt

    def _dead(pid, sig):
        killed.append((pid, sig))
        raise ProcessLookupError(pid)

    monkeypatch.setattr(
        spawn_module.subprocess, "Popen", lambda *a, **k: _ReapedThenInterrupted()
    )
    monkeypatch.setattr(spawn_module.os, "killpg", _dead)

    with pytest.raises(KeyboardInterrupt):
        _spawn(spawn_module, tmp_path)

    assert killed == []


def test_an_interrupt_as_the_grace_wait_returns_skips_the_escalation(
    tmp_path, monkeypatch
):
    """The escalation must not fire at a child SIGTERM had already finished off.

    Unlike the double-tap this needs only a *single* Ctrl-C: the timeout path
    enters the cleanup on its own, SIGTERM works, the grace wait reaps the
    child -- and the interrupt lands as that wait returns. Escalating then sends
    SIGKILL to a pid that no longer exists, and the ``ProcessLookupError``
    replaces the operator's stop exactly as it did one frame up.
    """
    killed: list[tuple[int, int]] = []

    class _ReapedByTheGraceWait:
        """Time out first, then reap on the grace wait and interrupt at once."""

        pid = 4242
        calls = 0
        returncode = None

        def wait(self, timeout=None):
            type(self).calls += 1
            if type(self).calls == 1:
                raise subprocess.TimeoutExpired(cmd="true", timeout=timeout)
            type(self).returncode = 0
            raise KeyboardInterrupt

    def _killpg_of_a_dead_pid(pid, sig):
        killed.append((pid, sig))
        if _ReapedByTheGraceWait.returncode is not None:
            raise ProcessLookupError(pid)

    monkeypatch.setattr(
        spawn_module.subprocess, "Popen", lambda *a, **k: _ReapedByTheGraceWait()
    )
    monkeypatch.setattr(spawn_module.os, "killpg", _killpg_of_a_dead_pid)

    with pytest.raises(KeyboardInterrupt):
        _spawn(spawn_module, tmp_path)

    assert killed == [(4242, signal.SIGTERM)]


def test_a_systemexit_kills_the_group_too(tmp_path, monkeypatch):
    """Not only Ctrl-C: any abnormal exit must take the session with it.

    A SIGTERM handler installed at the CLI entry point raises ``SystemExit``
    through this code, and an orphaned group costs the same either way.
    """
    killed: list[tuple[int, int]] = []

    class _Exiting:
        """Exit on the first wait, then reap on the group-kill wait."""

        pid = 4242
        calls = 0
        returncode = None

        def wait(self, timeout=None):
            type(self).calls += 1
            if type(self).calls == 1:
                raise SystemExit(1)
            return 0

    monkeypatch.setattr(spawn_module.subprocess, "Popen", lambda *a, **k: _Exiting())
    monkeypatch.setattr(
        spawn_module.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    with pytest.raises(SystemExit):
        _spawn(spawn_module, tmp_path)

    assert killed and killed[0] == (4242, signal.SIGTERM)
