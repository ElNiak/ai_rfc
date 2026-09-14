"""Plumbing the lifecycle verbs share: the config argument, and the config pair.

Every verb reads the config the operator passed and the copy ``init`` sealed,
and compares them. What they do about a refused identity field is where they
part: ``run`` refuses to touch a workspace whose identity moved, while
``status`` and ``verify`` exist precisely to report that it did. So the load is
:func:`load_pair` and the refusal is :func:`load_sealed` on top of it, rather
than one function with a flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..config import ReconConfig, drift, load_config
from ..diagnostics import StructuredDiagnostic
from ..driver import printable
from . import LifecycleError
from .workspace import Layout

CONFIG_ENV = "AI_RFC_CONFIG"


def report(message: str) -> None:
    """Diagnostics to stderr, as one printable line.

    The ``panther.*`` loggers swallow warnings, so these go straight to the
    stream the operator is watching.

    **Escaped here rather than at each of the twenty-nine call sites**, for
    the reason :func:`ai_rfc.driver.sweep.report` gives for the other stderr
    boundary: it is a boundary rather than a rule each caller remembers.
    (Twenty-nine is every ``report(`` outside this module, which is what a
    reader can re-derive; the two inside it are
    :func:`report_diagnostic`'s.) Every caller interpolates operator- or
    agent-controlled values into these lines — a config path, a cluster id, a
    ``--until`` bound, a toolchain path, the text of a caught error — and a
    value carrying a newline forges a second line beneath the first. That
    matters most where the real artifact is itself a line an operator reads as
    the tool's own verdict: a forged ``resume: ai-rfc …`` is a fabricated
    instruction, which is exactly what a ``--until cluster:<id>`` refusal
    produced before this became a boundary, and a forged ``note: gate clean``
    is what an unescaped ``cluster_id`` produced in ``draft/cli.py`` before
    that verb was routed here too.

    **The callers are no longer only the lifecycle verbs.** ``draft/cli.py``
    is a substrate command and reaches this through a function-local import,
    so the sentence to hold on to is the property, not the package: this is
    where a composed one-record diagnostic goes.

    No caller loses anything: none of these diagnostics is deliberately
    multi-line, and each is one record by construction.

    Args:
        message: The line to print.
    """
    print(printable(message), file=sys.stderr)


def report_structured(message: str) -> None:
    """A diagnostic whose **line structure is its own**, printed with it intact.

    :func:`report` collapses every line break, which is right for a line a
    verb *composed* — one record, with values interpolated into it — and wrong
    for text a tool or a parser *emitted*. Three producers are the second kind,
    and each has its **own exception type**, all three mixing in
    :class:`~ai_rfc.diagnostics.StructuredDiagnostic` so that a handler can
    test for the property instead of naming the types it hopes it caught:

    * :class:`~ai_rfc.toolchain.ToolchainBuildError` — ``toolchain.py`` passes
      the tail as the block (``ToolchainBuildError("make deps failed:",
      stderr[-2000:])``) and that tail is the only account of a failed build.
      It is a *subclass* because nine of the twelve ``ToolchainError`` sites
      interpolate a value instead, and routing the whole ``except`` clause
      once let raw ``git`` stderr forge a ``resume:`` line;
    * :class:`~ai_rfc.config.ConfigParseError` and
      :class:`~ai_rfc.ledger.LedgerParseError` — :class:`yaml.YAMLError` from
      ``recon.yaml`` and from ``revisions.yaml``, whose block ends in a ``^``
      under the offending column. A caret on a collapsed line points at
      nothing, and position is the whole of its meaning.

    **Calling this is an assertion by the caller**: that the breaks in this
    text are the producer's structure rather than a value that arrived from a
    ``recon.yaml``, a timeline or a model. Prefer :func:`report_diagnostic`,
    which makes the assertion where it is true — at the raise site, over the
    producer's half alone — rather than over a whole caught message. Call this
    directly only where the text is a producer's and nothing else is joined to
    it; the only such caller left is :func:`report_diagnostic` itself. Every
    other unprintable character
    is still escaped, line by line, and only ``\\n`` survives — so a ``\\r``,
    a NEL, a LINE SEPARATOR, an ANSI introducer or a RIGHT-TO-LEFT OVERRIDE
    smuggled into a tool's output cannot rewrite what the terminal shows. The
    exemption is exactly one character wide.

    A single-line message prints identically to :func:`report`, so the
    difference only ever appears where a break already exists.

    Args:
        message: The diagnostic, whose ``\\n`` breaks are kept.
    """
    # One trailing break dropped, not stripped: a subprocess's
    # ``stderr[-2000:]`` almost always ends in a newline, and ``str.split``
    # answers that with a final empty string — a blank stderr line after every
    # failed build. A *second* trailing break is a real blank line and is kept.
    for line in message.removesuffix("\n").split("\n"):
        print(printable(line), file=sys.stderr)


def report_diagnostic(prefix: str, error: BaseException) -> None:
    """Report one caught error, keeping a producer's own breaks and no others.

    The verb to use is a property of the **raise site**, so this asks the raise
    site: an exception mixing in
    :class:`~ai_rfc.diagnostics.StructuredDiagnostic` has already separated the
    half it composed from the half a parser or a build tool emitted, and each
    half gets the treatment it needs — the heading through :func:`report`, the
    block through :func:`report_structured`. Everything else is one record and
    is collapsed.

    This is what an ``except`` clause cannot do. A clause catches a *family*,
    and both mistakes this replaces came from asking it to: naming
    :class:`~ai_rfc.config.ConfigParseError` in a clause and passing the whole
    message to :func:`report_structured` exempted the config **path** along
    with the parser's block, so a path containing a newline forged a second
    line; widening to a bare ``except Exception`` and passing everything to
    :func:`report` collapsed a :class:`~ai_rfc.ledger.LedgerParseError`'s caret
    instead. One ``isinstance`` over the category answers both.

    Use it in the broad clause too. That is the point: the clause no longer
    has to know which types it caught.

    Args:
        prefix: Text the caller composed, such as ``"error: "``. Escaped.
        error: The caught exception.
    """
    if isinstance(error, StructuredDiagnostic):
        report(f"{prefix}{error.structured_context}")
        # A producer that wrote nothing has no block, and ``"".split("\n")``
        # is ``[""]`` — a blank stderr line under the heading. While the two
        # halves were one string the heading sat in front of that emptiness
        # and ``removesuffix`` consumed it; apart, they do not, so the
        # emptiness is answered where both halves are in hand.
        if error.structured_block:
            report_structured(error.structured_block)
        return
    report(f"{prefix}{error}")


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """The one argument every lifecycle verb shares."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"recon.yaml (default: ${CONFIG_ENV}).",
    )


def config_path_from(args: argparse.Namespace) -> Path:
    """``--config`` or ``$AI_RFC_CONFIG``; nothing is guessed.

    Args:
        args: The parsed arguments of any lifecycle verb.

    Returns:
        The configuration file to read.

    Raises:
        LifecycleError: If neither the flag nor the variable names one.
    """
    if args.config is not None:
        return args.config
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    raise LifecycleError(f"no config: pass --config or set {CONFIG_ENV}")


def load_pair(
    config_path: Path,
) -> tuple[ReconConfig, ReconConfig, Layout, list[str], list[str]]:
    """Load the config as given and as sealed, and compare them.

    The layout comes from the config as given, never from the sealed copy: a
    campaign pristine is sealed with its own root written into ``workspace:``,
    so reading the layout back out of the seal would send every verb to the
    tree the pristine was copied from.

    Args:
        config_path: The file the operator passed.

    Returns:
        ``(given, sealed, layout, refused, noted)`` — ``refused`` lists the
        identity fields that moved (D57) and ``noted`` every other change,
        both as ``path: old -> new`` lines. Judging them is the caller's.

    Raises:
        LifecycleError: If the workspace was never initialised.
        ConfigError: If either file does not validate.
    """
    given = load_config(config_path)
    layout = Layout(given.workspace)
    if not layout.init_record.exists() or not layout.config.exists():
        raise LifecycleError(
            f"{layout.root} is not an initialised workspace; "
            f"run: ai-rfc init --config {config_path}"
        )
    sealed = load_config(layout.config)
    refused, noted = drift(sealed, given)
    return given, sealed, layout, refused, noted


def load_sealed(
    config_path: Path,
) -> tuple[ReconConfig, ReconConfig, Layout, list[str]]:
    """Load the config pair as :func:`load_pair` does, refusing identity drift.

    For the verbs that act on a workspace rather than report on one: a
    reconstruction whose pin, window or draft name moved is a different
    reconstruction, so there is nothing safe to perform against it.

    Args:
        config_path: The file the operator passed.

    Returns:
        ``(given, sealed, layout, noted)`` — ``noted`` lists non-identity
        drift lines.

    Raises:
        LifecycleError: If the workspace was never initialised or an identity
            field drifted.
        ConfigError: If either file does not validate.
    """
    given, sealed, layout, refused, noted = load_pair(config_path)
    if refused:
        raise LifecycleError(
            "config drift refused (re-run ai-rfc init into a new workspace to "
            "change these): " + "; ".join(refused)
        )
    return given, sealed, layout, noted
