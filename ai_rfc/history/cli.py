"""Command-line entry point for corpus extraction."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_rfc import __version__

from .git_log import DEFAULT_FILE_CAP, GitError, extract
from .index import build_index
from .store import write_corpus


def _report(message: str) -> None:
    """Write a diagnostic to stderr.

    Deliberately not the ``logging`` module. Every ``panther.*`` logger is
    configured with ``propagate=False`` and a handler admitting only ``ERROR``,
    so a logged warning here is discarded before anyone sees it.
    """
    print(message, file=sys.stderr)


def configure(parser: argparse.ArgumentParser) -> None:
    """Add this command's arguments to ``parser``.

    Args:
        parser: Either the root's subparser for this command or the standalone
            parser :func:`build_standalone_parser` builds; both must carry the
            same arguments, so both are configured here.
    """
    parser.description = (
        "Extract a repository's commit history into a deterministic JSONL "
        "corpus, with an optional SQLite index for querying."
    )
    parser.add_argument("repo", type=Path, help="Path to an existing clone.")
    parser.add_argument(
        "--out", type=Path, required=True, help="Directory for the corpus."
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=DEFAULT_FILE_CAP,
        help=(
            "Maximum file rows recorded per commit. Commits above it are "
            "recorded with their true file count and flagged as truncated."
        ),
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Write the JSONL corpus without building the SQLite index.",
    )


def build_standalone_parser() -> argparse.ArgumentParser:
    """Build the parser ``python -m ai_rfc.history`` uses.

    Returns:
        A parser carrying this command's own ``prog`` and ``--version``, over
        the arguments the root mounts through :func:`configure`.
    """
    parser = argparse.ArgumentParser(prog="ai-rfc history")
    parser.add_argument(
        "--version", action="version", version=f"ai-rfc history {__version__}"
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    """Extract a repository into a corpus directory.

    Args:
        args: The parsed arguments, from either door.

    Returns:
        0 on success, 1 if the repository could not be read — which includes a
        shallow clone, whose truncated history would otherwise pass silently.
    """
    try:
        commits, changes, report = extract(args.repo, cap=args.cap)
    except GitError as error:
        _report(f"error: {error}")
        return 1

    write_corpus(commits, changes, report, args.out)
    if not args.no_index:
        build_index(args.out)

    if report.truncated_count:
        _report(
            f"note: {report.truncated_count} commit(s) exceeded the "
            f"{args.cap}-file cap and were truncated; their true file counts "
            f"are recorded in {report.commit_count} commit records"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command from the command line (``python -m ai_rfc.history``).

    Args:
        argv: Argument vector; ``None`` reads ``sys.argv``.

    Returns:
        The command's exit code.
    """
    return run(build_standalone_parser().parse_args(argv))
