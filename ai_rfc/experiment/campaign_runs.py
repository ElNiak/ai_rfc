"""Drive a campaign's frozen run order: copy, launch, record, resume.

Order comes from ``campaign.json`` and is never recomputed. A run with a
status record is skipped on resume; a run directory without one is an
interrupted launch and is refused rather than reused — it is evidence.

The name says what this manages: the campaign's frozen runs, whose matrix is
the :class:`~experiment.config.Campaign`. Do not confuse it with
:mod:`ai_rfc.driver`, the lower layer this calls to launch one session.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from ai_rfc.driver.arms import arm_profile

from . import ExperimentError
from .config import Campaign
from .runner import RunStatus, launch, load_status, run_ref
from .workspace import copy_workspace, verify_digest

#: What :func:`config.run_order` emits and nothing else: an arm letter and a
#: repeat number. ``fullmatch`` rather than a ``$``-anchored search, because
#: ``$`` also matches before a trailing newline — which is precisely the
#: character this guard exists to refuse. ``[0-9]`` rather than ``\d``, because
#: on a ``str`` pattern ``\d`` also matches every Unicode decimal digit and
#: ``int()`` accepts those too — so ``A\N{ARABIC-INDIC DIGIT ONE}`` would pass
#: both this guard and ``split_run_id`` while naming a directory no run has.
_RUN_ID = re.compile(r"[A-Z][0-9]+")


def checked_run_id(run_id: str) -> str:
    """The id, confirmed to be shaped like one before it is used as one.

    Public because both launch paths owe it: the sweep below, and
    ``cli._run_one_consolidation``, which reaches the same two sinks with a
    ``--only`` value. One path validating while its sibling does not is worse
    than neither doing so, because a reader assumes the pair agree.

    Membership of ``run_order`` cannot be the guard here: the order is itself
    read back out of ``campaign.json``, so it is the untrusted source rather
    than the closed set to check against. The value then reaches two sinks. It
    is joined into ``campaign.runs_dir / run_id``, where a ``..`` or a leading
    ``/`` leaves the campaign's directory; and it is interpolated into the
    progress lines an operator watches for hours, where a newline forges extra
    lines that read as the launcher's own. ``split_run_id`` does not cover
    either: it checks membership, then ``int(run_id[1:])`` — and ``int``
    accepts surrounding whitespace, so ``"A1\\n"`` parses as repeat 1.

    Args:
        run_id: The id as ``campaign.json`` recorded it.

    Returns:
        ``run_id`` unchanged.

    Raises:
        ExperimentError: If it is not an arm letter followed by digits.
    """
    if not _RUN_ID.fullmatch(run_id):
        # Repr, not the bare value: this message is itself one of the lines a
        # forged id would forge.
        raise ExperimentError(
            f"run id {run_id!r} is not an arm letter and a repeat number; it "
            "would reach a run directory path and an operator's progress lines "
            "unescaped"
        )
    return run_id


def pending_runs(campaign: Campaign) -> list[str]:
    """Run ids in frozen order that have no status record yet.

    Args:
        campaign: The frozen campaign.

    Returns:
        The ids still to launch, in frozen order.

    Raises:
        ExperimentError: If the frozen order holds an id that is not one.
    """
    return [
        run_id
        for run_id in campaign.run_order
        if load_status(campaign.runs_dir / checked_run_id(run_id)) is None
    ]


def launch_pending(
    campaign: Campaign,
    *,
    only: Iterable[str] | None = None,
    report: Callable[[str], None] = print,
) -> list[RunStatus]:
    """Launch every pending run in the frozen order (or the subset in ``only``).

    Args:
        campaign: The frozen campaign.
        only: Restrict to these run ids; None means the whole order.
        report: Called with one progress line per event.

    Returns:
        One status per selected run, launched or loaded, in frozen order.

    Raises:
        ExperimentError: If ``only`` names a run outside the campaign, the
            frozen order holds an id that is not one, or a run directory exists
            without a status record.
    """
    wanted = set(only) if only is not None else set(campaign.run_order)
    unknown = wanted - set(campaign.run_order)
    if unknown:
        raise ExperimentError(f"not in this campaign: {sorted(unknown)}")
    # The run order is frozen against one template, and every run copies from
    # it. A template regenerated mid-campaign renumbers the clusters that order
    # refers to, so the digest is re-checked here rather than only at init.
    drift = verify_digest(campaign.pristine_dir)
    if drift:
        raise ExperimentError(
            f"pristine workspace {campaign.pristine_dir} no longer matches the "
            f"digest this campaign froze: {'; '.join(drift)}"
        )

    statuses: list[RunStatus] = []
    for run_id in campaign.run_order:
        if run_id not in wanted:
            continue
        # Ahead of both sinks: the id is joined into this run's directory by
        # `run_ref`, and printed into every progress line below.
        ref = run_ref(campaign, checked_run_id(run_id))
        existing = load_status(ref.run_dir)
        if existing is not None:
            report(
                f"{run_id}: already ran (exit {existing.exit_code}, "
                f"timed_out={existing.timed_out}); skipping"
            )
            statuses.append(existing)
            continue
        if ref.run_dir.exists():
            raise ExperimentError(
                f"{ref.run_dir} exists without a status record; move it aside "
                f"(it is evidence of an interrupted launch) before resuming"
            )
        ref.run_dir.mkdir(parents=True)
        copy_workspace(campaign.pristine_dir, ref.workspace)
        report(
            f"{run_id}: launching arm {ref.arm} - {arm_profile(ref.arm).label}"
            f", repeat {ref.repeat} of {campaign.repeats}"
        )
        # A per-cluster sweep runs for hours. The transcript is the only thing
        # that moves while it does, and an operator who does not know its name
        # cannot tell a working run from a stalled one.
        report(
            f"{run_id}: transcript at {ref.run_dir} "
            f"(events.jsonl, sessions.jsonl, summaries/)"
        )
        status = launch(campaign, ref, report=report)
        report(
            f"{run_id}: exit {status.exit_code} timed_out={status.timed_out} "
            f"budget_hit={status.budget_hit}"
        )
        statuses.append(status)
    return statuses
