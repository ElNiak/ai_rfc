"""``ai-rfc status --config``.

Stages, the cluster ledger, the init record and config drift, in one view.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ... import __version__, ledger
from ...config import ConfigError, ConfigParseError
from ...pipeline.cli import print_status, status_payload
from .. import LifecycleError
from ..common import (
    add_config_argument,
    config_path_from,
    load_pair,
    report,
    report_structured,
)


def payload(config_path: Path) -> dict:
    """Everything ``status`` prints, as one dict.

    Args:
        config_path: The operator's ``recon.yaml``.

    Returns:
        The pipeline's own status body, extended with the cluster ledger, the
        init record, config drift and whether sessions are configured.

    Raises:
        LifecycleError: If the workspace was never initialised.
        ConfigError: If either the given or the sealed config does not validate.
        ledger.LedgerError: If the workspace's progress cannot be read.
    """
    # `load_pair`, not `load_sealed`: reporting a refused identity field is
    # this verb's job, so raising on one would hide exactly what was asked for.
    given, _sealed, layout, refused, noted = load_pair(config_path)
    # A timeline that was never built is a state the stage table already
    # reports, not an unreadable ledger; asking the ledger for it anyway turns
    # `status` on a freshly initialised workspace into the error it exists to
    # describe. A clusters.jsonl that is present but corrupt still raises.
    clustered = (layout.root / ledger.CLUSTERS_FILE).exists()
    states = ledger.clusters(layout.root) if clustered else ()
    nxt = ledger.next_cluster(layout.root) if clustered else None
    body = status_payload(layout.root)
    body.update(
        {
            "ledger": ledger.counts(states),
            "partial": [
                {"id": s.id, "ordinal": s.ordinal, "reason": s.partial_reason}
                for s in states
                if s.partial_reason
            ],
            "next_cluster": (
                None if nxt is None else {"id": nxt.id, "ordinal": nxt.ordinal}
            ),
            "init": json.loads(layout.init_record.read_text()),
            "drift": {"refused": refused, "noted": noted},
            "sessions": given.sessions is not None,
            "clustered": clustered,
        }
    )
    return body


def configure(parser: argparse.ArgumentParser) -> None:
    """Arguments of ``ai-rfc status``."""
    parser.description = (
        "Where the reconstruction stands: stage states, the cluster ledger, "
        "the pin, config drift."
    )
    add_config_argument(parser)
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Machine-readable output."
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """The parser ``python -m ai_rfc.lifecycle.status`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc status")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc status {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Print the status; 1 when the workspace or config cannot be read.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 on any refusal.
    """
    try:
        body = payload(config_path_from(args))
    except (ConfigParseError, ledger.LedgerParseError) as error:
        # Both parse blocks: `recon.yaml`'s and `revisions.yaml`'s. `status`
        # reads the ledger, so unlike `verify` it can meet the second.
        report_structured(f"error: {error}")
        return 1
    except (LifecycleError, ConfigError, ledger.LedgerError, OSError) as error:
        report(f"error: {error}")
        return 1
    if args.as_json:
        print(json.dumps(body, indent=2, sort_keys=True))
        return 0
    print_status(body)
    counts = body["ledger"]
    print(
        f"clusters: {counts['done']} of {counts['in_window']} done, "
        f"{counts['partial']} partial, {counts['outstanding']} outstanding"
    )
    for entry in body["partial"]:
        print(
            f"  partial: {entry['id']} (ordinal {entry['ordinal']}): {entry['reason']}"
        )
    nxt = body["next_cluster"]
    # "next cluster", not "next": `print_status` above already printed a
    # `next:` line naming the next stage, and the two answer different
    # questions. `run` names this one the same way.
    if not body["clustered"]:
        print("next cluster: none yet; the timeline has not been built")
    elif nxt is None:
        print("next cluster: every in-window cluster is done")
    else:
        print(f"next cluster: {nxt['id']} (ordinal {nxt['ordinal']})")
    record = body["init"]
    print(f"pin: {record['resolved_pin']}  forge: {record['forge_snapshot'] or 'none'}")
    for line in body["drift"]["refused"]:
        print(f"drift (refused): {line}")
    for line in body["drift"]["noted"]:
        print(f"drift (noted): {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.lifecycle.status``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
