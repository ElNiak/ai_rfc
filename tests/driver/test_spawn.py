import signal
import subprocess

import pytest

from ai_rfc import cli
from ai_rfc.driver import DriverError
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


def test_the_sigterm_handler_kills_the_group_too(tmp_path, monkeypatch):
    """Not only Ctrl-C: SIGTERM must take the session with it as well.

    Driven through the handler ``ai_rfc.cli`` really installs, rather than
    through an injected exception. The earlier spelling raised ``SystemExit``
    here and said in its own docstring that "a SIGTERM handler installed at
    the CLI entry point raises ``SystemExit`` through this code" — while no
    handler was installed anywhere, so SIGTERM killed the driver outright and
    nothing ever raised through this frame. The test passed on the strength of
    ``except BaseException`` catching an exception no production path
    produced: it proved the breadth of the clause, not the behaviour it was
    written to certify.

    What the handler raises is deliberately not restated here. It is
    ``KeyboardInterrupt`` so that one path serves both signals, and a test
    that hard-coded the type would keep passing if the install were removed.
    """
    killed: list[tuple[int, int]] = []

    class _Signalled:
        """Take the signal on the first wait, then reap on the group-kill wait."""

        pid = 4242
        calls = 0
        returncode = None

        def wait(self, timeout=None):
            type(self).calls += 1
            if type(self).calls == 1:
                # What the kernel does to a process running under the
                # disposition `ai_rfc.cli.main` installs for SIGTERM.
                return cli._interrupted(signal.SIGTERM, None)
            return 0

    monkeypatch.setattr(spawn_module.subprocess, "Popen", lambda *a, **k: _Signalled())
    monkeypatch.setattr(
        spawn_module.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )

    with pytest.raises(BaseException) as raised:
        _spawn(spawn_module, tmp_path)

    assert not isinstance(raised.value, Exception), "the stop must not be catchable"
    assert killed and killed[0] == (4242, signal.SIGTERM)


def test_a_first_session_refuses_to_truncate_an_existing_transcript(
    tmp_path, monkeypatch
):
    """``append=False`` means "this is the run's first session", never "erase".

    A run's first session opened its transcript for writing, so a second launch
    of the same run directory truncated the first's ``events.jsonl`` before its
    own process even existed. The live parser then raised on the one malformed
    line the truncation left, the cost fell to $0.00, and every session after it
    was handed a fresh budget -- ``mark-dry-49-51``, $28.51 against an $8 cap.

    ``Popen`` is replaced by something that cannot be called, so "nothing was
    spent" is asserted rather than inferred: the refusal has to land before the
    process exists, not after it returns.
    """
    events = tmp_path / "events.jsonl"
    held = b'{"type": "system", "subtype": "init"}\n{"type": "result"}\n'
    events.write_bytes(held)

    def _never(*_args, **_kwargs):
        raise AssertionError("a process was started over a held transcript")

    monkeypatch.setattr(spawn_module.subprocess, "Popen", _never)

    with pytest.raises(DriverError) as raised:
        _spawn(spawn_module, tmp_path)

    message = str(raised.value)
    assert str(events) in message, message
    assert f"mv {tmp_path} {tmp_path}.interrupted-" in message, message
    assert events.read_bytes() == held


def test_a_continuing_session_still_appends_to_the_transcript(tmp_path):
    """The other half of the same rule: a run of several sessions is one file.

    Driven through a real process rather than a stub, because what is being
    asserted is the mode the file is opened in and a stub would open nothing.
    """
    events = tmp_path / "events.jsonl"
    held = b'{"type": "result"}\n'
    events.write_bytes(held)

    exit_code, timed_out = spawn_module.spawn(
        ["true"],
        cwd=tmp_path,
        env={},
        events_path=events,
        stderr_path=tmp_path / "stderr.log",
        timeout_s=30,
        append=True,
    )

    assert (exit_code, timed_out) == (0, False)
    assert events.read_bytes() == held
