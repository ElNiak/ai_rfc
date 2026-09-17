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

from ai_rfc.driver import DriverError
from ai_rfc.driver.stream import (
    assistant_text,
    init_event,
    parse_stream,
    result_event,
)

from ...lifecycle.profile import profile_env
from .. import ExperimentError

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


class ClaudeCliSurfaceError(ClaudeCliError):
    """Raised when a session does not report the empty surface it was launched
    with.

    A probe for this row had a model claim tools its argv had not given it, so
    what the flags asked for is not evidence of what the session got. The init
    event is, and a session that reports nothing has reported nothing: an
    absent key and a null one are refused exactly like a populated one.

    The parent's "a judge that raises scores that one claim zero" does not
    hold for this subclass, and must not: a contaminated session did not
    produce a low score, it produced no score, so averaging one in would bias
    the run downward while looking like data. ``optimize.judge.build_judge``
    lets this one class out of its batch instead.
    """


#: The keys an init event must carry, each as an empty list, for a call to be
#: taken as isolated.
_SURFACE_KEYS = ("tools", "mcp_servers")


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


def _cost(final: dict[str, Any]) -> float | None:
    """What a result event says the call cost, when it says it in a number.

    A reply is never refused over this field. Nobody has measured the
    stream-json result event's shape on 2.1.272, the installed version, so a
    CLI that omits this key or spells it differently has to stay usable. What
    that costs is a figure nobody measured, and this returns it as unmeasured
    rather than as one a manifest could carry.

    Args:
        final: The result event.

    Returns:
        The cost, or ``None`` when the event carried no ``total_cost_usd``, a
        null, or anything that is not a number. ``bool`` is not a number here:
        ``True`` would otherwise be recorded as one dollar.
    """
    value = final.get("total_cost_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class ClaudeCliCall:
    """One ``claude -p`` per call, on a model, through a profile.

    Holds nothing a deep copy cannot carry -- strings, paths and numbers, and
    the JSON one session reported: a run's settings are deep-copied into
    ``result.json`` at the end, and its ``repr`` is what lands there.

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
        system_prompt: Sent as ``--system-prompt`` when given, so a role a
            call must hold never competes with the prompt on stdin. Carried
            on its own rather than on ``union_argv``, so naming one is never
            silently dropped by the regime the call runs under.
        union_argv: Whether the argv also carries the two flags the judge's
            regime adds. The GEPA proposer's argv was measured without them
            and was not part of that ruling, so it passes False. Named for
            what it gates and nothing more: every call, in either regime,
            refuses a session that reports a surface.

    Attributes:
        last_cost_usd: What the last call cost, per its result event; ``None``
            before the first call, after a call that failed, and after one
            whose result reported no usable figure.
        last_init: The init event the last call's session reported, whole and
            as reported; ``None`` before the first call and after a call that
            failed. It is what a run manifest records the scores' conditions
            from, which is why it carries what the session said rather than
            what the argv asked: a manifest built from ``--model`` would name
            the model requested, not the one that answered. It is also why no
            key is defaulted -- a key the session never reported is missing
            from this mapping rather than present and empty, so a reader can
            tell a surface measured empty from one never measured at all.
            Never an init :meth:`_check_surface` refused, since it is what
            that returned.
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
        system_prompt: str | None = None,
        union_argv: bool = True,
    ) -> None:
        self.claude_bin = claude_bin
        self.profile_dir = profile_dir
        self.model = model
        self.cwd = cwd
        self.effort = effort
        self.timeout_s = timeout_s
        self.system_prompt = system_prompt
        self.union_argv = union_argv
        self.last_cost_usd: float | None = None
        self.last_init: dict[str, Any] | None = None

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

        Under ``union_argv`` the vector is the union of that set and the one
        the design spec verified on 2.1.259, neither being a superset of the
        other. D-37 measured eight of the flags below as *accepted* on the
        installed 2.1.272 -- the two this gate adds, plus ``--setting-sources``,
        ``--safe-mode``, ``--no-session-persistence``, ``--system-prompt``,
        ``--effort`` and ``--tools``; the rest of the vector was not part of
        that measurement. What the two added ones *prevent* is measured
        nowhere, and the init assertion in :meth:`__call__` is what decides
        whether a session was actually isolated.

        Returns:
            The argv, the binary first.
        """
        argv = [
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
        if self.union_argv:
            argv += ["--strict-mcp-config", "--exclude-dynamic-system-prompt-sections"]
        if self.system_prompt is not None:
            argv += ["--system-prompt", self.system_prompt]
        return argv

    def env(self) -> dict[str, str]:
        """The child's whole environment; nothing else is inherited."""
        return profile_env(self.profile_dir)

    def _check_surface(
        self,
        events: list[dict[str, Any]],
        *,
        exit_code: int | None = None,
        stderr_tail: str = "",
    ) -> dict[str, Any]:
        """Refuse a session that did not report holding nothing.

        The shape is :func:`ai_rfc.experiment.preflight._arm_surface_check`'s
        -- two of the three init keys it reads, taken the same way round --
        without its defaulting: that check reads a whole probe matrix and
        reports what it found, while this one decides a single call and so has
        to tell a key reported empty from a key never reported at all.

        Args:
            events: The call's parsed stream-json events.
            exit_code: The child's exit status, carried onto a refusal so it
                reads like its six sibling failures. Always zero in practice,
                since a non-zero exit raises before this is reached.
            stderr_tail: What the child wrote to stderr, carried for the same
                reason.

        Returns:
            The accepted init event. A caller records this rather than reading
            the stream a second time, so what it records can never be an init
            this refused.

        Raises:
            ClaudeCliSurfaceError: If the session wrote no events at all, sent
                no init event, omitted either key from its init, or reported
                either as anything other than an empty list.
        """
        init = init_event(events)
        if init is None:
            # This runs before the result-event check, so a session that
            # exited clean having written nothing lands here rather than
            # there. "No init event" would be true of it and still not say
            # what happened, so the two states are named apart.
            what = "wrote no events at all" if not events else "sent no init event"
            raise ClaudeCliSurfaceError(
                f"{self!r} ran a session that {what}, so nothing it held "
                "was measured",
                exit_code=exit_code,
                stderr_tail=stderr_tail,
            )
        missing = [key for key in _SURFACE_KEYS if key not in init]
        if missing:
            raise ClaudeCliSurfaceError(
                f"{self!r} ran a session whose init omits {', '.join(missing)}, "
                "so what it held was not measured",
                exit_code=exit_code,
                stderr_tail=stderr_tail,
            )
        for key in _SURFACE_KEYS:
            # The reported value itself decides, not a helper's reading of it:
            # ``stream.mcp_servers`` keeps only the entries that are mappings
            # and defaults an absent key to ``{}``, so a guard written on its
            # result takes a list of bare names for no servers at all.
            value = init[key]
            if isinstance(value, list) and not value:
                continue
            raise ClaudeCliSurfaceError(
                f"{self!r} ran a session reporting {key}={json.dumps(value)}; "
                "an isolated session reports an empty list",
                exit_code=exit_code,
                stderr_tail=stderr_tail,
            )
        return init

    def __call__(self, prompt: str | list[dict[str, Any]]) -> str:
        """Send one prompt and return the model's reply text.

        A call that returns leaves :attr:`last_init` and :attr:`last_cost_usd`
        describing that call; one that raises leaves both cleared.

        Args:
            prompt: The text, or a chat-messages list as gepa may pass one.

        Returns:
            The result event's text.

        Raises:
            ClaudeCliError: On a binary that is missing or not executable, a
                cwd that cannot be created, a non-zero exit, a timeout,
                output that is not stream-json, a result marked as an error,
                or an empty reply.
            ClaudeCliSurfaceError: If the session did not report holding
                nothing.
        """
        text = prompt if isinstance(prompt, str) else _flatten(prompt)
        # Cleared before the call, not after it: one wrapper serves every call
        # of a run, so anything left standing past its own call would be read
        # as the next one's.
        self.last_cost_usd = None
        self.last_init = None
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
            # has. A failed call may equally leave stdout partial, so a parse
            # failure here is not itself the error worth reporting.
            try:
                events = parse_stream(completed.stdout)
            except (ExperimentError, DriverError):
                events = []
            raise ClaudeCliError(
                f"{self!r} exited {completed.returncode}: {tail}"
                f"{_quota_suffix(events)}",
                exit_code=completed.returncode,
                stderr_tail=tail,
            )
        try:
            events = parse_stream(completed.stdout)
        except (ExperimentError, DriverError) as error:
            raise ClaudeCliError(
                f"{self!r} wrote something that is not stream-json: {error}",
                exit_code=completed.returncode,
                stderr_tail=tail,
            ) from None
        init = self._check_surface(
            events, exit_code=completed.returncode, stderr_tail=tail
        )
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
        # Both below the empty-reply refusal rather than beside the events
        # they come from, so that every raise leaves them cleared: a figure or
        # a surface recorded by a call that then raised would stand as the
        # last good call's.
        self.last_cost_usd = _cost(final)
        self.last_init = init
        return reply
