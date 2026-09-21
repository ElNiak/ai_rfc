"""Whether a checkpoint is covered by a record the session cannot have forged.

``draft/checkpoint.py`` stores a manifest's digest *beside* the manifest, so
whoever writes the directory writes both and the check passes by construction.
That detects tampering after the fact and cannot detect fabrication. The remedy
ruled on 2026-09-15 relocates the authority instead of hardening the artifact:
a checkpoint is **covered** when something outside the directory says it was
produced, and **unverified** otherwise.

Three routes say so, and this module decides which:

``session``
    A ``sessions.jsonl`` row carrying the cluster id. :data:`SESSION_ROW_KEYS
    <ai_rfc.driver.record.SESSION_ROW_KEYS>` includes ``cluster_id``, so the
    row links a session to the cluster it was launched for.
``pre_seed``
    The harness's own marker inside the checkpoint directory, which says the
    harness put it there and no session claimed it.
``receipt``
    A ``tool_result`` carrying ``note: checkpoint written to …/checkpoints/<id>``
    whose invoking ``tool_use`` is itself a checkpoint-write call. The writer
    emits that line only after a successful write and refuses an existing
    directory (``draft/checkpoint.py:99-103``), so the receipt proves
    **production** rather than mention — and mention runs at 106–110 cluster
    ids per transcript against 10 produced.

**Uncovered means unverified, never forged.** A session row is appended only
after the process returns, so a killed session legitimately leaves none.

Two properties hold this module in place.

*The route is recorded, never ranked.* A row proves a session was **launched
for** a cluster; a receipt proves **this call created that directory**. Neither
is the weaker substitute. Only one value fits per checkpoint, so the routes are
evaluated in the order the ruling states them — session, pre-seed, receipt —
and that order is what makes the record reproducible, not a claim about
strength.

*Candidates come from the caller.* Nothing here resolves a path, enumerates a
run directory or opens a transcript on its own account, and nothing here writes
anywhere. ``ai-rfc verify`` supplies a production workspace's own
``runs/*/events.jsonl``; the campaign audit supplies a run's siblings, because
in the campaign layout the transcript is a sibling of the workspace rather than
inside it. Teaching this module either layout would put the directory shape of
one caller into the predicate both use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .. import ledger
from . import DriverError, printable
from .arms import arm_profile
from .enforcement import FILTERS, SUBSTITUTION, bash_prefixes, command_groups
from .stream import parse_stream, tool_results, tool_uses

#: The verb every write shape names, whichever door it comes through.
CHECKPOINT_VERB = "checkpoint"
#: The directory a receipt's path must sit in for its last segment to be a
#: cluster id. The join is this tail and nothing above it; see :func:`receipts`.
CHECKPOINTS_DIR = "checkpoints"
#: What a transcript that could not be read is rendered as. It is never a
#: finding and never an error: absence and damage cover nothing, and saying so
#: is what keeps an uncovered checkpoint from reading as tampering.
CANNOT_ADJUDICATE = "cannot adjudicate"
#: The receipt, verbatim from both writers (``draft/cli.py`` and
#: ``server/core/gates.py``). The text is invariant across both eras below.
RECEIPT = "checkpoint written to"
#: Stop at a quote, comma or whitespace: the note is embedded in a JSON string
#: for arms A and B and in bare stdout for arm C.
_RECEIPT_PATH = re.compile(RECEIPT + r" ([^\s\"',\\]+)")


class Route(Enum):
    """Which record covered a checkpoint. Recorded, never ranked."""

    session = "session"
    pre_seed = "pre_seed"
    receipt = "receipt"


@dataclass(frozen=True)
class Era:
    """How one era spelled the checkpoint-write call, in all three arms."""

    first_seen: str
    closed: str | None
    closed_by: str | None
    mcp_tool: str
    cli_prefixes: tuple[str, ...]
    module_forms: tuple[str, ...]


#: **The one bounded exception to ADR 0002**, and the reason it is bounded.
#:
#: Every other set this package gates on is derived from the tree: the entry
#: points from the modules on disk, arm B's command prefix from
#: :func:`~ai_rfc.driver.arms.arm_profile`. This table ranges over *eras*, and
#: the tree only knows the present — no guard can derive a spelling that no
#: longer exists in the source. So the rule is amended rather than violated
#: silently: derive the set where the set is derivable; where it is historical,
#: **date it and say what closed it**.
#:
#: Era 1 is closed by the ``panther/plugins/services/testers/a_rfc`` →
#: ``…/ai_rfc`` rename, which landed as PANTHER ``17a99e079`` on 2026-09-01;
#: the last campaign run under it is ``pilot-aioquic-w02-11-20260831``. Era 2
#: takes arm B's prefix from the arm profile rather than spelling it, so the
#: live half of the table cannot drift. Two entries; a third would mean a third
#: era exists, and then it needs its own closing event. Each era carries a
#: *tuple* of command prefixes for the same reason ``arms.py`` states: how
#: many an arm declares is that module's business, not a reader's.
ALIASES: tuple[Era, ...] = (
    Era(
        first_seen="2026-08-28",
        closed="2026-09-01",
        closed_by=(
            "the panther/plugins/services/testers/a_rfc -> ai_rfc rename "
            "(PANTHER 17a99e079)"
        ),
        mcp_tool="mcp__arfc__arfc_checkpoint",
        cli_prefixes=("arfc ",),
        module_forms=(".draft checkpoint", " draft checkpoint"),
    ),
    Era(
        first_seen="2026-09-01",
        closed=None,
        closed_by=None,
        mcp_tool="mcp__ai_rfc__ai_rfc_checkpoint",
        cli_prefixes=bash_prefixes(arm_profile("B")),
        module_forms=(".draft checkpoint", " draft checkpoint"),
    ),
)


def _is_cluster_argument(token: str) -> bool:
    """Whether a token can be the cluster id a write call named.

    This is a *shape* test, not the id's grammar. The grammar is declared once,
    where ids are minted (``timeline/build.py``), and a second copy here would
    drift; what has to be refused is an option where an argument belongs —
    ``arfc checkpoint --help`` is a real call in the archive, and an extractor
    that took the third token read ``--help`` as a cluster id.

    Args:
        token: The token a write shape put in the cluster's position.

    Returns:
        True when the token is a non-empty positional naming one path segment.
    """
    return bool(token) and not token.startswith("-") and "/" not in token


def _sole_stage(command: str) -> str | None:
    """The single command a Bash write call runs, read the guard's own way.

    A compound or mixed command is **never** a write:
    ``ai-rfc checkpoint c0002 ; echo 'note: checkpoint written to …'`` is a
    write-shaped call whose receipt the shell produced. Command substitution
    fails closed for the reason :func:`~ai_rfc.driver.enforcement.is_allowed`
    gives — a prefix check cannot see what ``$(...)`` would run — and so does
    an unterminated quote.

    Args:
        command: The raw ``tool_input.command`` string.

    Returns:
        The one command the call runs, or ``None`` when it runs more than one,
        hides one, or cannot be read.
    """
    if any(token in command for token in SUBSTITUTION):
        return None
    try:
        groups = command_groups(command)
    except ValueError:
        return None
    if len(groups) != 1:
        return None
    stages = groups[0]
    # A pager reads the group's output and reaches nothing new, exactly as the
    # guard and the audit's `bash_surface` already read it.
    if any(stage.split()[0] not in FILTERS for stage in stages[1:]):
        return None
    return stages[0]


def _verb_argument(stage: str, prefix: str, verb: str) -> str | None:
    """The first argument of ``<prefix><verb>``, when that is what ``stage`` is.

    Args:
        stage: One command, already known to run alone.
        prefix: The era's command prefix, trailing space included.
        verb: The subcommand the prefix is followed by.

    Returns:
        The first argument, ``""`` when the verb took none, or ``None`` when
        this stage is not that command at all.
    """
    head = f"{prefix}{verb}"
    if not stage.startswith(head):
        return None
    rest = stage[len(head) :]
    if rest and not rest[:1].isspace():
        return None
    parts = rest.split()
    return parts[0] if parts else ""


def _cluster_of_call(
    name: str, tool_input: Mapping[str, Any]
) -> tuple[str, str] | None:
    """The arm whose write shape a call matches, and the cluster it named.

    Args:
        name: The tool's name.
        tool_input: The call's input.

    Returns:
        ``(arm, cluster_id)``, or ``None`` when the call is not a checkpoint
        write in any era's spelling.
    """
    for era in ALIASES:
        if name == era.mcp_tool:
            cluster = str(tool_input.get("cluster_id") or "")
            return ("A", cluster) if _is_cluster_argument(cluster) else None
    if name != "Bash":
        return None
    stage = _sole_stage(str(tool_input.get("command", "")).strip())
    if stage is None:
        return None
    for era in ALIASES:
        # Every prefix the era declares is tried: the cardinality is
        # ``arms.py``'s business, and a reader that took only the first would
        # stop seeing arm B's calls the day it declares a second family.
        for prefix in era.cli_prefixes:
            argument = _verb_argument(stage, prefix, CHECKPOINT_VERB)
            if argument is not None:
                return ("B", argument) if _is_cluster_argument(argument) else None
    for era in ALIASES:
        # Both invocation forms name the same call: the module form
        # (`ai_rfc.draft checkpoint`, still a valid direct invocation) and the
        # dispatcher form (`ai_rfc draft checkpoint`) the arm-C prompt instructs.
        if not any(form in stage for form in era.module_forms):
            continue
        parts = stage.split()
        if "--cluster" not in parts:
            continue
        index = parts.index("--cluster") + 1
        cluster = parts[index] if index < len(parts) else ""
        return ("C", cluster) if _is_cluster_argument(cluster) else None
    return None


def checkpoint_calls(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every checkpoint-write call in a transcript, in stream order.

    Arm-free and era-aware: a run is one arm, but a reader that took the arm as
    an argument could only ever see the shape it was told to expect, and the
    archive's three shapes predate today's spellings.

    Args:
        events: The parsed transcript.

    Returns:
        One record per call — ``index``, ``id`` (so the call joins to
        :func:`~ai_rfc.driver.stream.tool_results`, which is keyed by
        ``tool_use_id``), ``arm`` (the shape it matched) and ``cluster_id``.
    """
    calls = []
    for use in tool_uses(events):
        matched = _cluster_of_call(use["name"], use["input"])
        if matched is None:
            continue
        arm, cluster = matched
        calls.append(
            {
                "index": use["index"],
                "id": use["id"],
                "arm": arm,
                "cluster_id": cluster,
            }
        )
    return calls


def _digest_of(text: str) -> str | None:
    """The ``manifest_sha256`` a receipt's envelope carries, if it has one.

    Arms A and B answer with a JSON envelope; arm C answers with a bare stdout
    line and binds no content at all.
    """
    try:
        body = json.loads(text)
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    digest = body.get("manifest_sha256")
    return digest if isinstance(digest, str) else None


def receipts(
    events: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str | None], str]:
    """Every checkpoint a transcript proves was produced, and by what path.

    A receipt counts only when its invoking ``tool_use`` is itself a write call
    and names the same cluster the receipt does: a ``cat`` of an old log can
    reproduce the text, and a write of one cluster says nothing about another.

    **The join is the ``checkpoints/<id>`` tail, never the absolute path.** Two
    experiments roots exist, the ``a_rfc`` → ``ai_rfc`` rename rewrote repo
    paths embedded in transcripts, and — measured on ``mark-dry-49-51`` —
    :func:`~ai_rfc.driver.record.move_aside` renames a run directory *after*
    its receipts were written, so every receipt in both ``A1.interrupted-*``
    transcripts names ``runs/A1/workspace/checkpoints``, a directory that no
    longer exists. Which transcripts are candidates is the caller's decision;
    this only reads them.

    Args:
        events: The parsed transcript.

    Returns:
        ``(cluster_id, manifest_sha256)`` to the receipt's path as written. The
        digest is ``None`` for arm C, which emits no envelope. The key carries
        the cluster id because a digest is not unique: an unchanged manifest
        checkpointed against two clusters gives both the same one.
    """
    calls = {call["id"]: call for call in checkpoint_calls(events)}
    found: dict[tuple[str, str | None], str] = {}
    for use_id, result in tool_results(events).items():
        call = calls.get(use_id)
        if call is None or result["is_error"]:
            continue
        digest = _digest_of(result["text"])
        for path in _RECEIPT_PATH.findall(result["text"]):
            tail = PurePosixPath(path)
            if tail.parent.name != CHECKPOINTS_DIR:
                continue
            if tail.name != call["cluster_id"]:
                continue
            found[(tail.name, digest)] = path
    return found


def read_transcript(transcript: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Parse one candidate strictly, naming the damage instead of raising.

    The parse is strict, following the audit's policy — a garbled transcript
    must not be read as evidence — and not the salvage policy
    :func:`~ai_rfc.driver.record.spent` uses, which is the policy chosen for
    money and progress. But the refusal must not escape: ``ai-rfc verify`` maps
    any exit code that is not 0 or 3 to 1, so a detector that let it through
    would turn a clean workspace into an error.

    Absence is ordinary rather than suspicious — a hand-driven or MCP-only
    workspace never gets a ``runs/`` directory at all — so it is named the same
    way and means the same thing: **this route covered nothing**. The
    checkpoints it would have covered stay uncovered and reach the operator
    through the ordinary finding.

    Args:
        transcript: One candidate ``events.jsonl``.

    Returns:
        The events and ``None``, or ``[]`` and one ``cannot adjudicate: …``
        line naming the transcript and the damage.
    """
    try:
        text = transcript.read_text(errors="replace")
    except OSError as error:
        return [], printable(f"{CANNOT_ADJUDICATE}: {transcript}: {error}")
    try:
        return parse_stream(text), None
    except DriverError as error:
        return [], printable(f"{CANNOT_ADJUDICATE}: {transcript}: {error}")


def _checkpoint_dirs(checkpoints_dir: Path) -> list[Path]:
    """The written checkpoints under a checkpoints root, in name order.

    A directory holding no ``checkpoint.json`` is not a checkpoint: it is what
    an interruption left, and :func:`ai_rfc.draft.completeness.checkpoint_records`
    already skips it on the same condition. Counting one here would report
    every interrupted run as harbouring an unverified artifact.
    """
    try:
        entries = sorted(checkpoints_dir.iterdir())
    except OSError:
        return []
    return [
        entry
        for entry in entries
        if entry.is_dir() and (entry / ledger.CHECKPOINT_FILE).is_file()
    ]


def covers(
    checkpoints_dir: Path,
    *,
    session_rows: Iterable[Mapping[str, Any]],
    transcripts: Iterable[Sequence[Mapping[str, Any]]],
) -> dict[str, Route | None]:
    """Which route covers each checkpoint, and which checkpoint has none.

    Args:
        checkpoints_dir: The workspace's ``checkpoints/`` directory. It is read
            and never written.
        session_rows: Parsed ``sessions.jsonl`` rows, from the caller. A row
            naming no cluster — a consolidation session's — covers nothing.
        transcripts: Already-parsed candidate transcripts, from the caller.
            :func:`read_transcript` is how a caller turns a path into one
            without letting a parse error escape; a transcript it could not
            read contributes an empty event list, which covers nothing.

    Returns:
        One entry per written checkpoint: the route that covered it, or
        ``None`` when nothing did. ``None`` means **unverified**, never forged.
    """
    by_row = {
        row["cluster_id"]
        for row in session_rows
        if isinstance(row.get("cluster_id"), str) and row["cluster_id"]
    }
    by_receipt = {
        cluster for events in transcripts for cluster, _digest in receipts(events)
    }
    routes: dict[str, Route | None] = {}
    for entry in _checkpoint_dirs(checkpoints_dir):
        if entry.name in by_row:
            routes[entry.name] = Route.session
        elif (entry / ledger.PRESEED_MARKER).exists():
            routes[entry.name] = Route.pre_seed
        elif entry.name in by_receipt:
            routes[entry.name] = Route.receipt
        else:
            routes[entry.name] = None
    return routes
