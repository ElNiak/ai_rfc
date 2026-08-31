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
    """The final result event, if any.

    A transcript stitched from several sessions carries one result event each,
    and this returns only the last. Use :func:`result_events` for anything that
    must account for all of them — cost and permission denials above all, where
    taking the last silently reports one session's figure as the run's.
    """
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return None


def result_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every result event, in stream order.

    One per session. A run that spawns an agent per cluster produces as many as
    it has clusters.

    Args:
        events: The parsed transcript.

    Returns:
        The result events; empty when the transcript has none.
    """
    return [event for event in events if event.get("type") == "result"]


#: Fields summed across a run's sessions. Everything else is carried from the
#: last session, which is what describes how the run ended.
_SUMMED = ("total_cost_usd", "num_turns", "duration_ms", "duration_api_ms")

#: Fields whose numeric leaves are summed recursively. Both are nested and
#: mix counts with labels: ``usage`` holds token counts beside strings like
#: ``service_tier``, and ``modelUsage`` holds per-model counts keyed by id.
_SUMMED_TREES = ("usage", "modelUsage")


def _sum_tree(left: Any, right: Any) -> Any:
    """Add two usage trees, summing numeric leaves and keeping labels."""
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) or bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left + right
    if isinstance(left, dict) and isinstance(right, dict):
        return {
            key: (
                _sum_tree(left[key], right[key])
                if key in left and key in right
                else left.get(key, right.get(key))
            )
            for key in {**left, **right}
        }
    if isinstance(left, list) and isinstance(right, list):
        return left + right
    return left if left is not None else right


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Fold a run's per-session result events into one record.

    A single session is the identity case: the record is returned unchanged,
    down to the fields nothing here interprets. That matters because everything
    downstream — the cost table, the denial count, the campaign report — reads
    this one record and must not be able to tell how many sessions produced it.

    Costs and turn counts are summed, usage trees are summed leaf by leaf, and
    permission denials are concatenated. Taking the last session's figures
    instead would report a whole campaign's spend as its final cluster's.

    Args:
        results: The result events, in stream order.

    Returns:
        The merged record, or ``None`` when there are no results.
    """
    if not results:
        return None
    merged = dict(results[-1])
    if len(results) == 1:
        return merged

    for field in _SUMMED:
        values = [
            r.get(field) for r in results if isinstance(r.get(field), (int, float))
        ]
        merged[field] = sum(values) if values else None
    for field in _SUMMED_TREES:
        trees = [r[field] for r in results if isinstance(r.get(field), dict)]
        if trees:
            total = trees[0]
            for tree in trees[1:]:
                total = _sum_tree(total, tree)
            merged[field] = total
    merged["permission_denials"] = [
        denial
        for result in results
        for denial in (result.get("permission_denials") or [])
    ]
    merged["is_error"] = any(bool(r.get("is_error")) for r in results)
    failed = [r.get("subtype") for r in results if r.get("subtype") != "success"]
    merged["subtype"] = failed[0] if failed else "success"
    merged["session_count"] = len(results)
    return merged


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
