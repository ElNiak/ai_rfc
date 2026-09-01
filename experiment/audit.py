"""Classify a run's tool calls by surface and judge arm integrity.

An *executed* call on a surface outside the run's arm is an integrity
violation (impossible by construction, still checked). A *denied* call is a
bypass attempt, kept as data. Errors split into the class-1 channel (typed
tool errors) and the class-2 channel (shell errors), as the protocol's
two-sided taxonomy asks.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import ExperimentError
from .arms import RAW_PREFIX, arm_profile
from .config import Campaign
from .enforcement import FILTERS, bash_prefixes, command_groups, is_allowed
from .runner import EVENTS_FILE, GUARD_FILE, load_status
from .stream import (
    is_denial,
    merge_results,
    parse_stream,
    pretooluse_hook_starts,
    result_events,
    tool_results,
    tool_uses,
)

STATE_FILES = ("manifest.yaml", "questions.yaml", "revisions.yaml")
ALLOWED_SURFACES: dict[str, set[str]] = {
    "A": {"mcp", "edit", "read"},
    "B": {"bash:arfc", "edit", "read"},
    "C": {"bash:python_a_rfc", "bash:git", "bash:sqlite3", "edit", "read"},
}


@dataclass(frozen=True)
class ToolCall:
    """One classified tool call."""

    index: int
    name: str
    surface: str
    #: The program family within the surface, e.g. ``arfc`` inside
    #: ``bash:arfc``. Recorded key: it is written into ``audit/<run_id>.json``
    #: through ``asdict``, so the field keeps the word the evidence uses.
    family: str
    target: str
    denied: bool
    errored: bool
    in_arm: bool
    summary: str
    path: str = ""


def _stage_surface(stage: str) -> str:
    """The surface one pipe stage reaches for."""
    if stage.startswith("arfc "):
        return "bash:arfc"
    if stage.startswith(RAW_PREFIX):
        return "bash:python_a_rfc"
    if stage.startswith("git "):
        return "bash:git"
    if stage.startswith("sqlite3 "):
        return "bash:sqlite3"
    return "bash:other"


def bash_surface(command: str) -> str:
    """The bash surface a shell line reaches; a mixed line is named as such.

    The split is the guard's own, so the audit reads a command the same way
    the enforcement did. Reading it any other way makes the two disagree: a
    quoted SQL argument holding ``;`` or ``||``, or a command paging its own
    output through ``| head``, is one in-prefix command to the guard, and
    counting it as ``bash:mixed`` would report an integrity violation for a
    call the arm was entitled to make.

    Args:
        command: The raw ``tool_input.command`` string.

    Returns:
        One of ``bash:arfc``, ``bash:python_a_rfc``, ``bash:git``,
        ``bash:sqlite3``, ``bash:other`` or ``bash:mixed``.
    """
    try:
        groups = command_groups(command)
    except ValueError:
        return "bash:other"
    surfaces = set()
    for stages in groups:
        surfaces.add(_stage_surface(stages[0]))
        # A pager reads the group's output; it reaches no new surface.
        surfaces.update(
            _stage_surface(stage)
            for stage in stages[1:]
            if stage.split()[0] not in FILTERS
        )
    if not surfaces:
        return "bash:other"
    return surfaces.pop() if len(surfaces) == 1 else "bash:mixed"


def edit_target(file_path: str, workspace: Path) -> str:
    """Where an edit landed, resolved against the run's own workspace.

    Read from the workspace layout rather than from the path's shape. A
    basename match alone counts an edit to any file called ``manifest.yaml``
    anywhere on disk as a register hand-edit, and a ``/draft/`` substring
    counts any markdown file under any directory called ``draft``. Both feed
    published measurements, so a workspace laid out differently — or a stray
    edit outside it — silently changes the numbers.

    Args:
        file_path: The edit's target as the transcript recorded it.
        workspace: The run's workspace root.

    Returns:
        ``register``, ``prose``, or ``other``.
    """
    if not file_path:
        return "other"
    candidate = Path(file_path)
    if not candidate.is_relative_to(workspace):
        return "other"
    parts = candidate.relative_to(workspace).parts
    if len(parts) == 1 and parts[0] in STATE_FILES:
        return "register"
    if len(parts) == 2 and parts[0] == "draft" and parts[1].endswith(".md"):
        return "prose"
    return "other"


def classify(
    name: str, tool_input: dict[str, Any], workspace: Path
) -> tuple[str, str, str]:
    """Return ``(surface, family, target)`` for one tool call.

    Args:
        name: The tool's name as the stream reports it.
        tool_input: The call's input object.
        workspace: The run's workspace root, against which edit targets are
            resolved.

    Returns:
        The surface it reached for, the family within that surface, and the
        edit target (``register``, ``prose`` or ``other``) where it applies.
    """
    if name.startswith("mcp__arfc__"):
        return "mcp", name[len("mcp__arfc__") :], ""
    if name.startswith("mcp__"):
        return "mcp:other", name, ""
    if name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        surface = bash_surface(command)
        return surface, command.split(" ", 1)[0] if command else "", ""
    if name in ("Edit", "Write", "MultiEdit"):
        path = str(tool_input.get("file_path", ""))
        return "edit", name, edit_target(path, workspace)
    if name in ("Read", "Grep", "Glob"):
        return "read", name, ""
    return "other", name, ""


def in_arm(name: str, tool_input: dict[str, Any], surface: str, arm: str) -> bool:
    """Whether one call stayed inside ``arm``, read the way the guard reads it.

    For ``Bash`` the verdict comes from :func:`enforcement.is_allowed` — the
    same function the live ``PreToolUse`` guard runs — rather than from the
    surface label. The label collapses a command to a single surface, so a
    command whose groups reach two prefixes the arm *holds* (``sqlite3
    -version; git ...`` in arm C) labels as ``bash:mixed`` and, tested against
    the label, was reported as a violation the arm was entitled to make. The
    label stays as reporting detail; only the decision moved.

    Args:
        name: The tool's name as the stream reports it.
        tool_input: The call's input object.
        surface: The surface :func:`classify` assigned it.
        arm: The arm the run was launched as.

    Returns:
        True when the arm was entitled to make this call.
    """
    if name == "Bash":
        command = str(tool_input.get("command", "")).strip()
        return is_allowed(command, bash_prefixes(arm_profile(arm)))
    return surface in ALLOWED_SURFACES[arm]


def _summary(use: dict[str, Any]) -> str:
    tool_input = use["input"]
    for key in ("command", "file_path", "cluster_id", "claim_id", "tag"):
        if key in tool_input:
            value = str(tool_input[key])
            if len(value) <= 120:
                return f"{key}={value}"
            # Keep the tail of a path: the basename is the identifying half.
            head = "..." if key == "file_path" else ""
            kept = value[-117:] if key == "file_path" else value[:120]
            return f"{key}={head}{kept}"
    return json.dumps(tool_input, sort_keys=True)[:120]


def _denied_ids(events: list[dict[str, Any]]) -> set[str]:
    """The ids of calls the CLI itself reported as denied.

    Measured on 2.1.247: ``permission_denials`` carries ``tool_use_id``, which
    links a denial to the exact call it refused. That is authoritative and needs
    no text matching; ``is_denial`` remains the fallback for a denial that never
    reached the result event.

    Args:
        events: The parsed transcript.

    Returns:
        The denied calls' ids.
    """
    final = merge_results(result_events(events)) or {}
    return {
        str(denial["tool_use_id"])
        for denial in final.get("permission_denials") or []
        if isinstance(denial, dict) and denial.get("tool_use_id")
    }


def guard_stats(
    events: list[dict[str, Any]], arm: str, recorded: str, mounted: str
) -> dict[str, Any]:
    """Whether the guard was mounted unmodified and actually ran.

    Two independent halves, because they fail differently. The digest catches
    a settings file edited after the run began; the hook count catches a guard
    that was never consulted at all. Neither is folded into ``integrity``: an
    unfired guard is not the same finding as an executed out-of-arm call, and
    a report that conflated them could not say which happened.

    Arm A declares no Bash prefix, so it has no Bash calls and fires no
    PreToolUse hook. That is the expected state for arm A, not a missing
    guard, and ``fired_for_every_bash_call`` is vacuously true there.

    ``fired_for_every_bash_call`` compares counts, not pairings: it asks
    whether at least as many PreToolUse hooks began as there were Bash calls.
    It therefore catches a guard that never ran, and cannot by itself catch a
    guard that ran for most calls and was somehow skipped for one. Both raw
    counts are reported so a reader can check the equality the pilot expects.

    Args:
        events: The parsed transcript.
        arm: The arm the run was launched as.
        recorded: The digest ``status.json`` recorded when the guard was
            written, or ``""`` for a run predating the field.
        mounted: The digest of the settings file as it stands now.

    Returns:
        The guard section of the audit record.
    """
    bash_calls = sum(1 for use in tool_uses(events) if use["name"] == "Bash")
    starts = pretooluse_hook_starts(events)
    return {
        "recorded_sha256": recorded,
        "mounted_sha256": mounted,
        "unmodified": bool(recorded) and recorded == mounted,
        "digest_recorded": bool(recorded),
        "bash_calls": bash_calls,
        "pretooluse_hook_starts": starts,
        "fired_for_every_bash_call": starts >= bash_calls,
        "expected_no_bash": arm == "A",
    }


def audit_events(
    events: list[dict[str, Any]], arm: str, workspace: Path
) -> dict[str, Any]:
    """Audit one transcript for the arm it was supposed to stay inside.

    Args:
        events: The parsed transcript.
        arm: The arm the run was launched as.
        workspace: The run's workspace root, against which edit targets are
            resolved.

    Returns:
        The audit record.
    """
    results = tool_results(events)
    denied_ids = _denied_ids(events)
    calls: list[ToolCall] = []
    for use in tool_uses(events):
        surface, family, target = classify(use["name"], use["input"], workspace)
        result = results.get(str(use["id"]))
        errored = bool(result and result["is_error"])
        denied = str(use["id"]) in denied_ids or (errored and is_denial(result["text"]))
        calls.append(
            ToolCall(
                index=use["index"],
                name=use["name"],
                surface=surface,
                family=family,
                target=target,
                denied=denied,
                errored=errored,
                in_arm=in_arm(use["name"], use["input"], surface, arm),
                summary=_summary(use),
                path=str(use["input"].get("file_path", "")),
            )
        )
    violations = [c for c in calls if not c.in_arm and not c.denied]
    bypasses = [c for c in calls if c.denied]
    class1 = [
        c for c in calls if c.errored and not c.denied and c.surface.startswith("mcp")
    ]
    class2 = [
        c for c in calls if c.errored and not c.denied and c.surface.startswith("bash")
    ]
    failures = sorted(c.index for c in class1 + class2)
    final = merge_results(result_events(events)) or {}
    return {
        "arm": arm,
        "integrity": not violations,
        "tool_calls": {
            "total": len(calls),
            "by_surface": dict(sorted(Counter(c.surface for c in calls).items())),
        },
        "executed_out_of_arm": [asdict(c) for c in violations],
        "bypass_attempts": {
            "count": len(bypasses),
            "by_surface": dict(sorted(Counter(c.surface for c in bypasses).items())),
            "items": [asdict(c) for c in bypasses],
            "result_permission_denials": len(final.get("permission_denials") or []),
        },
        "errors": {
            "class1": len(class1),
            "class2": len(class2),
            "first_failure_index": failures[0] if failures else None,
        },
        "hand_edits": {
            name: sum(
                1
                for c in calls
                if c.surface == "edit"
                and c.target == "register"
                and c.path.rsplit("/", 1)[-1] == name
            )
            for name in STATE_FILES
        },
        "prose_edits": sum(
            1 for c in calls if c.surface == "edit" and c.target == "prose"
        ),
        "compaction_events": sum(
            1
            for event in events
            if event.get("type") == "system"
            and "compact" in str(event.get("subtype", ""))
        ),
        "api_errors": sum(1 for event in events if event.get("type") == "error")
        + (1 if final.get("is_error") else 0),
        "event_count": len(events),
    }


def audit_run(campaign: Campaign, run_id: str) -> dict[str, Any]:
    """Audit one run from its transcript and write ``audit/<run_id>.json``.

    Args:
        campaign: The frozen campaign.
        run_id: The run to audit.

    Returns:
        The audit record, also written to the campaign's audit directory.

    Raises:
        ExperimentError: If the run has no status record.
    """
    run_dir = campaign.runs_dir / run_id
    status = load_status(run_dir)
    if status is None:
        raise ExperimentError(f"{run_id} has no status record; nothing to audit")
    events = parse_stream((run_dir / EVENTS_FILE).read_text(errors="replace"))
    guard_path = run_dir / GUARD_FILE
    mounted = (
        hashlib.sha256(guard_path.read_bytes()).hexdigest()
        if guard_path.exists()
        else ""
    )
    audit = {
        "run_id": run_id,
        **audit_events(events, status.arm, run_dir / "workspace"),
        "guard": guard_stats(events, status.arm, status.guard_sha256, mounted),
    }
    campaign.audit_dir.mkdir(exist_ok=True)
    (campaign.audit_dir / f"{run_id}.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    return audit


def audit_campaign(campaign: Campaign) -> dict[str, dict[str, Any]]:
    """Audit every run that has a status record.

    Args:
        campaign: The frozen campaign.

    Returns:
        One audit record per completed run, keyed by run id.
    """
    return {
        run_id: audit_run(campaign, run_id)
        for run_id in campaign.run_order
        if load_status(campaign.runs_dir / run_id) is not None
    }
