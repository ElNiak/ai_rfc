"""Readers for the ``claude -p --output-format stream-json`` event stream.

One JSON object per line. The helpers tolerate absent fields — the stream's
shape is documented only in part, and spike S0 records the real one — but
never a malformed line, which is a harness failure worth stopping on.
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import ExperimentError

_DENIAL = re.compile(
    r"permission|not allowed|denied|not in the allowed|requires approval",
    re.IGNORECASE,
)
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def parse_stream(text: str) -> list[dict[str, Any]]:
    """Parse a stream-json transcript into its events.

    Raises:
        ExperimentError: If a non-blank line is not a JSON object.
    """
    events: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ExperimentError(
                f"stream line {lineno} is not JSON: {error}"
            ) from None
        if not isinstance(event, dict):
            raise ExperimentError(f"stream line {lineno} is not a JSON object")
        events.append(event)
    return events


def init_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The session's init event, if any."""
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def result_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The final result event, if any."""
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def _blocks(event: dict[str, Any]) -> list[Any]:
    content = (event.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    return "" if content is None else str(content)


def tool_uses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool call the model made, in stream order."""
    uses = []
    for index, event in enumerate(events):
        if event.get("type") != "assistant":
            continue
        for block in _blocks(event):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses.append(
                    {
                        "index": index,
                        "id": block.get("id"),
                        "name": str(block.get("name", "")),
                        "input": block.get("input") or {},
                    }
                )
    return uses


def tool_results(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every tool result keyed by the id of the call it answers."""
    results: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events):
        if event.get("type") != "user":
            continue
        for block in _blocks(event):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[str(block.get("tool_use_id"))] = {
                    "index": index,
                    "is_error": bool(block.get("is_error")),
                    "text": _text_of(block.get("content")),
                }
    return results


def is_denial(text: str) -> bool:
    """Whether an errored tool result reads as a permission denial.

    Args:
        text: The tool result's text.

    Returns:
        True when the text carries denial wording.
    """
    return bool(_DENIAL.search(text))


def denials(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Permission denials, from errored tool results and the result event."""
    found = []
    names = {use["id"]: use["name"] for use in tool_uses(events)}
    for use_id, result in tool_results(events).items():
        if result["is_error"] and is_denial(result["text"]):
            found.append(
                {
                    "source": "tool_result",
                    "tool": names.get(use_id),
                    "detail": result["text"][:200],
                }
            )
    final = result_event(events) or {}
    for denial in final.get("permission_denials") or []:
        if isinstance(denial, dict):
            found.append(
                {
                    "source": "result",
                    "tool": denial.get("tool_name"),
                    "detail": json.dumps(denial.get("tool_input"), sort_keys=True)[
                        :200
                    ],
                }
            )
    return found


def hook_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every hook activity event the CLI reported, in stream order.

    The CLI reports hook activity as ``system`` events carrying a ``hook_``
    subtype and a ``hook_event`` name, never as a dedicated top-level type.
    They arrive only when the launch passed ``--include-hook-events``.

    Args:
        events: The parsed transcript.

    Returns:
        The hook events, unfiltered by which hook fired.
    """
    return [
        event
        for event in events
        if str(event.get("subtype", "")).startswith("hook_") or "hook_event" in event
    ]


def pretooluse_hook_starts(events: list[dict[str, Any]]) -> int:
    """How many times a ``PreToolUse`` hook began running.

    Args:
        events: The parsed transcript.

    Returns:
        The count of ``hook_started`` events naming ``PreToolUse``.
    """
    return sum(
        1
        for event in hook_events(events)
        if event.get("subtype") == "hook_started"
        and str(event.get("hook_event", "")) == "PreToolUse"
    )


def assistant_text(events: list[dict[str, Any]]) -> str:
    """The model's visible text, joined in stream order."""
    parts = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in _blocks(event):
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def usage_series(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cumulative token usage after each distinct assistant message.

    A message split over several events (one per content block) carries the
    same ``message.id`` and usage; it is counted once.
    """
    series: list[dict[str, Any]] = []
    running = {key: 0 for key in _USAGE_KEYS}
    seen: set[str] = set()
    for index, event in enumerate(events):
        if event.get("type") != "assistant":
            continue
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        if not usage:
            continue
        message_id = message.get("id")
        if message_id is not None:
            if message_id in seen:
                continue
            seen.add(message_id)
        for key in _USAGE_KEYS:
            running[key] += int(usage.get(key, 0) or 0)
        series.append({"index": index, **running, "total": sum(running.values())})
    return series
