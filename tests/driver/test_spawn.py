import signal

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
