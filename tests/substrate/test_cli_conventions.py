"""Conventions every ai_rfc entry point holds to.

The substrate is a set of independent ``python -m`` commands that a human and
an agent both drive, so the properties that make them scriptable are worth
asserting once across all of them rather than once per file.
"""

import ast
import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

import ai_rfc
from ai_rfc import __version__
from ai_rfc.entrypoints import ENTRY_POINTS, PACKAGE
from ai_rfc.parser import Parser

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.prog for e in ENTRY_POINTS])
def test_every_entry_point_reports_its_version(entry, capsys):
    """Reproducibility is the stated point, so each command names its build."""
    with pytest.raises(SystemExit) as exit_info:
        entry.load().main(["--version"])
    assert exit_info.value.code == 0
    stdout = capsys.readouterr().out
    assert entry.prog in stdout
    assert __version__ in stdout


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.prog for e in ENTRY_POINTS])
def test_a_malformed_invocation_exits_two_everywhere(entry):
    """2 belongs to argparse alone; strict findings return 3.

    This is the half of the split that is easy to regress. Moving findings to 3
    is only useful if 2 keeps meaning "the command was wrong" — a caller that
    branches on the pair needs both halves to hold, and only the findings half
    has tests of its own.
    """
    with pytest.raises(SystemExit) as exit_info:
        entry.load().main(["--no-such-flag"])
    assert exit_info.value.code == 2


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.prog for e in ENTRY_POINTS])
def test_every_standalone_door_is_built_on_the_hardened_parser(entry):
    """``python -m ai_rfc.<sub>`` must refuse the way the root door refuses.

    :class:`ai_rfc.parser.Parser` overrides ``error()``, the one method every
    argparse diagnostic funnels through, so asserting the *type* covers each
    of those messages without enumerating them — the predicate-over-an-
    enumeration shape this file already uses for the register's ``_report``
    copies. The set asserted over is the registry's, which
    :func:`test_every_cli_module_on_disk_is_registered` holds to the tree, so
    a twenty-eighth command cannot land with a stock parser and no failing
    test. That is the whole remedy: the enumeration was already derived, only
    the assertion over it was missing, and every door built a bare
    :class:`argparse.ArgumentParser` until it existed.
    """
    parser = importlib.import_module(entry.module).build_standalone_parser()

    assert isinstance(parser, Parser)


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.prog for e in ENTRY_POINTS])
def test_importing_an_entry_point_does_not_run_it(entry):
    """``python -m`` must stay the only way these run.

    An unguarded ``__main__.py`` calls ``sys.exit(cli.main())`` at import time,
    so anything that merely imports it — a test, a driver, a documentation tool
    — exits the interpreter, parsing whatever ``sys.argv`` happened to hold.
    """
    name = f"{entry.module.rsplit('.', 1)[0]}.__main__"
    sys.modules.pop(name, None)

    importlib.import_module(name)


PACKAGE_ROOT = Path(ai_rfc.__file__).parent

#: Helpers the README's "Known duplication to consolidate" table tracks by
#: hand, keyed by the name in its first column. Every row naming a function
#: belongs here: a row left uncovered is the one that silently passes, and the
#: register then reads as verified while being wrong.
TRACKED_HELPERS = ("_report", "_git", "_digest", "_digest_bytes")


def _package_sources() -> list[Path]:
    """Every module the table speaks for: the substrate, minus the frontends.

    ``server`` is a separate program. **``experiment`` is no longer one** — it
    mounts as ``ai-rfc experiment`` — and it stays excluded here anyway, for a
    reason that belongs to this test rather than to the registry: the register
    being counted against is the README's, whose ``_report`` row (``README.md``
    line 516) names seven copies and not ``experiment/cli.py``'s — seven and
    not the eight it named until ``draft/cli.py``'s copy was replaced by
    ``lifecycle.common.report``. Counting a
    copy the register does not claim would fail
    ``test_the_duplication_table_names_every_copy`` against a README this
    exclusion has no standing to rewrite.

    So the two exclusions in this file no longer share a reason, and must not
    be changed together: ``test_every_cli_module_on_disk_is_registered`` asks
    which modules are *verbs*, and this one asks which copies the *register*
    speaks for.
    """
    return [
        path
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if not {"server", "experiment", "__pycache__"}
        & set(path.relative_to(PACKAGE_ROOT).parts)
    ]


def _defines(helper: str) -> set[str]:
    """Modules defining ``helper``, as paths relative to the package root."""
    pattern = re.compile(rf"^def {re.escape(helper)}\(", re.MULTILINE)
    return {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in _package_sources()
        if pattern.search(path.read_text())
    }


def _declared(helper: str) -> set[str]:
    """Modules the README's table lists as holding a copy of ``helper``.

    Anchored to the table's own heading rather than scanning the whole file:
    any other pipe-delimited line naming the same helper would otherwise shadow
    the real row, and the test would then assert against something that is not
    the register.
    """
    lines = (PACKAGE_ROOT / "README.md").read_text().splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("## Known duplication to consolidate")
    )
    for line in lines[start:]:
        if line.startswith("## ") and not line.startswith("## Known duplication"):
            break
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) > 2 and f"`{helper}`" in cells[1]:
            return set(re.findall(r"`([^`]+\.py)`", cells[2]))
    raise AssertionError(f"the duplication table has no row for {helper}")


@pytest.mark.parametrize("helper", TRACKED_HELPERS)
def test_the_duplication_table_names_every_copy(helper):
    """The table is only worth keeping if it is accurate.

    Its stated purpose is that accepted debt stays legible "rather than
    discovered twice", and it had already failed at that twice: five ``_report``
    copies were recorded against eight on disk, three ``_git`` against five, as
    ``coverage/``, ``forge/`` and ``pipeline/`` landed without anyone updating
    the row. A hand-maintained register of hand-maintained copies drifts unless
    something counts them, so this counts them.
    """
    defined = _defines(helper)
    # Guards the vacuous pass: a helper consolidated away, leaving an emptied
    # row nobody deleted, would otherwise match set() against set().
    assert defined, f"{helper} is in the table but defined nowhere"
    assert _declared(helper) == defined


def test_every_cli_module_on_disk_is_registered():
    """A sub-package nobody registers is the failure this file exists to stop.

    Single-sourcing the list only removes the second copy; it does not notice a
    ninth sub-package that never reached the first. This counts them, the way
    ``test_the_duplication_table_names_every_copy`` counts helper copies.

    **``experiment`` was exempt and no longer is.** It was exempt while it was
    a program of its own, reachable only as ``python -m ai_rfc.experiment``;
    spec D56 mounted it as ``ai-rfc experiment``, so it is now a verb the
    registry owes a row like any other, and counting it is what makes a future
    sub-package under ``experiment/`` — an ``experiment/report/cli.py``, say —
    fail here rather than ship unreachable. Narrowing the exemption *adds* what
    this assertion covers; it is not the weakening the exemption would be if
    ``experiment`` were still unregistered.

    ``server`` stays exempt for the structural reason, not an unexamined one:
    it is not a verb anybody types. It is launched as a stdio MCP server by the
    client that speaks to it — see the ``["-m", "ai_rfc.server"]`` argv in
    :mod:`ai_rfc.driver.arms` — so mounting it under ``ai-rfc`` would offer an
    operator a command whose stdout is a wire protocol.
    """
    on_disk = {
        PACKAGE + "." + ".".join(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
        for path in PACKAGE_ROOT.rglob("cli.py")
        # The package-root cli.py is the door that dispatches to these, not one
        # of them; the door has its own test module.
        if path != PACKAGE_ROOT / "cli.py"
        and not {"server"} & set(path.relative_to(PACKAGE_ROOT).parts)
    }
    assert on_disk == {entry.module for entry in ENTRY_POINTS}


def test_every_entry_declares_a_section():
    """An empty heading renders as a bare colon with its commands beneath it.

    Not a crash, which is why it is worth asserting: the listing still prints
    and still holds every command, under a heading that says nothing.
    """
    assert all(entry.section for entry in ENTRY_POINTS)


def test_entries_sharing_a_section_are_contiguous():
    """Declaration order is the help's order, so a section must not be split.

    ``_epilog()`` in ``ai_rfc/cli.py`` groups by section instead of printing a
    heading whenever it changes, so the failure a split now produces is the
    quieter one: rather than double-heading, the stray rows are pulled back
    silently under the earlier block and the listing stops matching the order
    the registry declares. That is a weaker symptom than the one this
    assertion was written against, which is the argument for keeping it.
    """
    runs: list[str] = []
    for entry in ENTRY_POINTS:
        if not runs or runs[-1] != entry.section:
            runs.append(entry.section)
    assert len(runs) == len(set(runs))


def test_main_is_the_standalone_door_over_configure_and_run(capsys):
    """``python -m ai_rfc.<sub> --help`` still works for every registered module."""
    for entry in ENTRY_POINTS:
        module = importlib.import_module(entry.module)
        with pytest.raises(SystemExit) as excinfo:
            module.main(["--help"])
        assert excinfo.value.code == 0, entry.verb
        assert f"usage: {entry.prog}" in capsys.readouterr().out, entry.verb


#: A message shaped like the forgery every escaped boundary exists to stop:
#: one record, a line ending, and a second line spelled as a verb's own
#: verdict. Built by concatenation — ``test_source_hygiene`` forbids a source
#: file carrying a character :meth:`str.isprintable` refuses.
_FORGED_TAIL = "note: 3 cluster view(s) written to /forged"


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=[e.prog for e in ENTRY_POINTS])
def test_a_refusal_at_a_standalone_door_cannot_forge_a_line(entry, capsys):
    """The behavioural twin of the type assertion, at every standalone door.

    :func:`test_a_malformed_invocation_exits_two_everywhere` already holds the
    exit code; this holds the *shape* of what reaches stderr on the way out.
    argparse composes ``unrecognized arguments: %s`` by interpolating the
    operator's own leftover tokens, so a token carrying a line ending writes a
    second line that reads as a verdict of the tool's own — the forgery
    ``tests/cli/test_root_errors.py`` pins at the root door, here pinned at
    the twenty-seven doors ``python -m ai_rfc.<sub>`` opens.

    Driven through ``error()`` with argparse's own composed message rather
    than through one argv, because **no single argv reaches that branch at
    every door** — measured over all 27 with the forged token as the sole
    argument: eight doors (``init run next status verify doctor gate
    citation-gate``) did print the forgery; thirteen answered ``invalid
    choice``, which interpolates with ``%r`` and forges nothing; five answered
    ``the following arguments are required``, which does not interpolate the
    token at all; and ``checkpoint`` *accepted* it as a positional, so driving
    ``main`` there would run the verb. An option-shaped token changes none of
    that: ``_parse_optional`` returns ``None`` for any argument containing a
    space, and a missing required argument is refused inside
    ``_parse_known_args`` before a leftover is ever looked at. The message is
    therefore handed to the funnel directly, which is the site the remedy
    lives at and the only one every door shares.
    """
    parser = importlib.import_module(entry.module).build_standalone_parser()

    with pytest.raises(SystemExit) as exit_info:
        parser.error("unrecognized arguments: evil" + chr(0x0A) + _FORGED_TAIL)

    assert exit_info.value.code == 2
    forged = [
        line for line in capsys.readouterr().err.splitlines() if _FORGED_TAIL in line
    ]
    # One line, and it is the diagnostic itself rather than a line standing
    # under it: the usage argparse prints first is genuinely two lines, so a
    # count over the whole capture would prove nothing.
    assert len(forged) == 1
    assert forged[0].startswith(f"{entry.prog}: error: ")
    assert "\\n" in forged[0]


@pytest.mark.parametrize("separator", [chr(0x0A), chr(0x0D), chr(0x2028)])
@pytest.mark.parametrize("module", sorted(_defines("_report")))
def test_every_report_helper_escapes_what_it_prints(module, separator, capsys):
    """A diagnostic is one line, and every copy of the helper makes it so.

    Parametrised over the register's own measured set rather than a written
    list, for the reason :func:`_defines` exists: a copy that lands without
    anyone updating a list is exactly the drift this file was written to
    catch, and an eighth ``_report`` arriving unescaped must fail here rather
    than be discovered by a forged line in somebody's terminal.

    Asserted at the **boundary**, which is where the remedy now lives. Each
    of these modules interpolates operator- or agent-controlled values into
    its diagnostics — a repository path, a cluster id, a caught error
    carrying ``git``'s own stderr — and escaping them one call site at a time
    is a rule every future author has to remember. Escaping here is a
    property of the function, and it is the shape
    :func:`ai_rfc.lifecycle.common.report` already gives the lifecycle verbs
    and ``draft/cli.py``.

    CR is in the enumeration beside LF because a terminal ends a line on it
    just as readily, and U+2028 because ``str.splitlines`` does — but the
    implementation is :func:`ai_rfc.driver.printable`'s predicate over the
    unprintable category, so these three are witnesses and not the rule.
    """
    imported = importlib.import_module(
        "ai_rfc." + module.removesuffix(".py").replace("/", ".")
    )
    imported._report("error: boom" + separator + _FORGED_TAIL)
    # `splitlines` first, then assert on the line: the trailing break `print`
    # itself writes is not the value's, and a check over the whole capture
    # fails on it for LF while passing for the two separators that matter
    # most — a test that is green for the wrong reason in two cases of three.
    lines = capsys.readouterr().err.splitlines()

    assert len(lines) == 1
    assert separator not in lines[0]
    assert _FORGED_TAIL in lines[0]


#: The probe :func:`test_a_substrate_doors_refusal_loads_no_lifecycle` runs in
#: a fresh interpreter. It must be a fresh one: by the time any test in this
#: file executes, a sibling has already imported ``ai_rfc.lifecycle``, so the
#: question cannot be asked in this process at all.
_LIFECYCLE_PROBE = """
import sys
import ai_rfc.forge.cli as door
before = sorted(n for n in sys.modules if n.startswith("ai_rfc.lifecycle"))
parser = door.build_standalone_parser()
try:
    parser.error("unrecognized arguments: x")
except SystemExit:
    pass
after = sorted(n for n in sys.modules if n.startswith("ai_rfc.lifecycle"))
print(repr((before, after)))
"""


def test_a_substrate_doors_refusal_loads_no_lifecycle():
    """``pipeline/cli.py:28-40`` states the invariant; ``error()`` broke it.

    ``Parser.error`` reached ``lifecycle.common.report`` for one line of
    output, so triggering an argparse error at any of the seven substrate
    doors imported the layer their own sibling docstring says the substrate
    may not import. The escape is identical either way; what changes is which
    layer a refusal drags in.
    """
    finished = subprocess.run(
        [sys.executable, "-c", _LIFECYCLE_PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    before, after = ast.literal_eval(finished.stdout.strip())

    assert before == [], before
    assert after == [], after
