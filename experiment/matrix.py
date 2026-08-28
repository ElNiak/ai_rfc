"""Execute a campaign's frozen run order: copy, launch, record, resume.

Order comes from ``campaign.json`` and is never recomputed. A run with a
status record is skipped on resume; a run directory without one is an
interrupted launch and is refused rather than reused — it is evidence.
"""

from __future__ import annotations

from typing import Callable, Iterable

from . import ExperimentError
from .config import Campaign
from .runner import RunStatus, launch, load_status, run_spec
from .workspace import copy_workspace


def pending_runs(campaign: Campaign) -> list[str]:
    """Run ids in frozen order that have no status record yet.

    Args:
        campaign: The frozen campaign.

    Returns:
        The ids still to launch, in frozen order.
    """
    return [
        run_id
        for run_id in campaign.run_order
        if load_status(campaign.runs_dir / run_id) is None
    ]


def execute(
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
        ExperimentError: If ``only`` names a run outside the campaign, or a
            run directory exists without a status record.
    """
    wanted = set(only) if only is not None else set(campaign.run_order)
    unknown = wanted - set(campaign.run_order)
    if unknown:
        raise ExperimentError(f"not in this campaign: {sorted(unknown)}")
    statuses: list[RunStatus] = []
    for run_id in campaign.run_order:
        if run_id not in wanted:
            continue
        spec = run_spec(campaign, run_id)
        existing = load_status(spec.run_dir)
        if existing is not None:
            report(
                f"{run_id}: already ran (exit {existing.exit_code}, "
                f"timed_out={existing.timed_out}); skipping"
            )
            statuses.append(existing)
            continue
        if spec.run_dir.exists():
            raise ExperimentError(
                f"{spec.run_dir} exists without a status record; move it aside "
                f"(it is evidence of an interrupted launch) before resuming"
            )
        spec.run_dir.mkdir(parents=True)
        copy_workspace(campaign.pristine_dir, spec.workspace)
        report(f"{run_id}: launching arm {spec.arm}, repeat {spec.repeat}")
        status = launch(campaign, spec)
        report(
            f"{run_id}: exit {status.exit_code} timed_out={status.timed_out} "
            f"budget_hit={status.budget_hit}"
        )
        statuses.append(status)
    return statuses
