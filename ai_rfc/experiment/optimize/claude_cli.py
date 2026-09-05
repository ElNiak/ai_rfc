"""``claude -p`` as a one-shot language model: the pilot's proposer and judge.

The optimizer's reflection LM and the judge's transport are the same shape,
one prompt in and one raw reply out, and both run through the CLI on the
experiment profile so that a pilot draws on the subscription behind it and
never on an API key. The prompt travels on stdin (a reflection prompt carries
four skill bodies), the reply is read from the stream-json result event, and
the child gets the five-variable environment every experiment session gets,
so nothing the shell holds reaches it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .. import ExperimentError
from ..profile import profile_env
from ..stream import assistant_text, parse_stream, result_event

#: The id form the CLI accepts for a proposer or judge that runs through
#: ``claude -p``; also what :class:`ClaudeCliCall` reports as its ``repr``,
#: which is what ``result.json`` records for a callable.
PREFIX = "claude-cli:"

#: How much of a failed call's stderr an error carries.
_STDERR_TAIL = 2000


def cli_model(value: str) -> str | None:
    """The model inside a ``claude-cli:<model>`` id.

    Args:
        value: A ``--reflection-lm`` or ``--judge-model`` argument.

    Returns:
        The model, or ``None`` when the id is not in that form and belongs to
        the litellm or API path.

    Raises:
        ExperimentError: If the form carries no model.
    """
    if not value.startswith(PREFIX):
        return None
    model = value.removeprefix(PREFIX).strip()
    if not model:
        raise ExperimentError(f"{value!r} names no model after {PREFIX}")
    return model


class ClaudeCliError(ExperimentError):
    """Raised when one ``claude -p`` call returns no usable reply.

    A proposer that raises stops the optimization, which is the intended
    reading: the run's own log names the cause and a relaunch under the same
    name resumes it. A judge that raises scores that one claim zero.
    """

    def __init__(
        self, message: str, *, exit_code: int | None = None, stderr_tail: str = ""
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


def quota(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last ``rate_limit_event``'s ``rate_limit_info``, if the stream had one.

    Under a subscription this is the only signal that a run is approaching
    its limit; a USD budget never fills.
    """
    for event in reversed(events):
        if event.get("type") == "rate_limit_event":
            info = event.get("rate_limit_info")
            return info if isinstance(info, dict) else {}
    return None


def _quota_suffix(events: list[dict[str, Any]]) -> str:
    """The clause naming the quota a stream carried, empty when it carried none."""
    limit = quota(events)
    return "" if limit is None else f" (rate limit: {json.dumps(limit)})"


def _flatten(messages: list[dict[str, Any]]) -> str:
    """One prompt from a chat-messages list, each turn under its role."""
    parts = []
    for message in messages:
        role = str(message.get("role", "user")).capitalize()
        content = message.get("content", "")
        if not isinstance(content, str):
            content = "\n".join(
                str(block.get("text", "")) if isinstance(block, dict) else str(block)
                for block in content
            )
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _decoded(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode("utf-8", "replace")


class ClaudeCliCall:
    """One ``claude -p`` per call, on a model, through a profile.

    Holds only strings, paths and numbers: a run's settings are deep-copied
    into ``result.json`` at the end, and its ``repr`` is what lands there.

    Args:
        claude_bin: The CLI to launch.
        profile_dir: The authenticated ``CLAUDE_CONFIG_DIR`` the call runs
            under; the subscription behind it is what the call draws on.
        model: The model id, as ``--model`` takes it.
        cwd: Where the child runs; created on the first call. Every
            customization source is disabled by the argv, so this only has
            to exist.
        effort: The CLI's ``--effort`` level.
        timeout_s: Seconds before the child is killed and the call raises.
    """

    def __init__(
        self,
        claude_bin: str,
        profile_dir: Path,
        model: str,
        *,
        cwd: Path,
        effort: str = "high",
        timeout_s: int = 120,
    ) -> None:
        self.claude_bin = claude_bin
        self.profile_dir = profile_dir
        self.model = model
        self.cwd = cwd
        self.effort = effort
        self.timeout_s = timeout_s

    def __repr__(self) -> str:
        return f"{PREFIX}{self.model}"

    def argv(self) -> list[str]:
        """The complete argument vector.

        Measured on Claude Code 2.1.260 (2026-09-04): with ``--safe-mode``
        alone the child still read an output style and plan mode from the
        settings on disk, and a judge answer cited "Plan mode active";
        ``--setting-sources ""`` and ``--permission-mode dontAsk`` closed
        that. ``--tools ""`` makes the proposer a model rather than an agent
        that could load the very skills it is rewriting.
        """
        return [
            self.claude_bin,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--safe-mode",
            "--setting-sources",
            "",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "",
            "--no-session-persistence",
        ]

    def env(self) -> dict[str, str]:
        """The child's whole environment; nothing else is inherited."""
        return profile_env(self.profile_dir)

    def __call__(self, prompt: str | list[dict[str, Any]]) -> str:
        """Send one prompt and return the model's reply text.

        Args:
            prompt: The text, or a chat-messages list as gepa may pass one.

        Returns:
            The result event's text.

        Raises:
            ClaudeCliError: On a binary that is missing or not executable, a
                cwd that cannot be created, a non-zero exit, a timeout,
                output that is not stream-json, a result marked as an error,
                or an empty reply.
        """
        text = prompt if isinstance(prompt, str) else _flatten(prompt)
        try:
            self.cwd.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                self.argv(),
                input=text,
                cwd=self.cwd,
                env=self.env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except OSError as failure:
            raise ClaudeCliError(
                f"{self!r} cannot run {self.claude_bin} in {self.cwd}: {failure}"
            ) from None
        except subprocess.TimeoutExpired as expired:
            tail = _decoded(expired.stderr)[-_STDERR_TAIL:]
            raise ClaudeCliError(
                f"{self!r} gave no reply within {self.timeout_s} s",
                stderr_tail=tail,
            ) from None
        tail = completed.stderr[-_STDERR_TAIL:]
        if completed.returncode != 0:
            # A hard usage-limit trip may exit non-zero with the quota event
            # already on stdout, and that window is the only meter this design
            # has; whatever else a failed call left there is not parseable.
            try:
                events = parse_stream(completed.stdout)
            except ExperimentError:
                events = []
            raise ClaudeCliError(
                f"{self!r} exited {completed.returncode}: {tail}"
                f"{_quota_suffix(events)}",
                exit_code=completed.returncode,
                stderr_tail=tail,
            )
        try:
            events = parse_stream(completed.stdout)
        except ExperimentError as error:
            raise ClaudeCliError(
                f"{self!r} wrote something that is not stream-json: {error}",
                exit_code=completed.returncode,
                stderr_tail=tail,
            ) from None
        final = result_event(events)
        if final is None:
            raise ClaudeCliError(
                f"{self!r} ended without a result event",
                exit_code=completed.returncode,
                stderr_tail=tail,
            )
        if final.get("is_error"):
            raise ClaudeCliError(
                f"{self!r} reported an error: {str(final.get('result', ''))[:500]}"
                f"{_quota_suffix(events)}",
                exit_code=completed.returncode,
                stderr_tail=tail,
            )
        reply = str(final.get("result") or assistant_text(events))
        if not reply.strip():
            raise ClaudeCliError(
                f"{self!r} returned an empty reply",
                exit_code=completed.returncode,
                stderr_tail=tail,
            )
        return reply
