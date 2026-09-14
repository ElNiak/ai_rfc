"""``draft``'s four agent verbs, folded into the leaf that already owned the name.

The other ten groups are packages of their own under ``ai_rfc/agent/``. This
one could not be: ``draft`` was already a top-level leaf verb with six
sub-verbs of its own, and two ``add_parser("draft")`` calls cannot coexist. So
``commit``, ``build``, ``lint`` and ``render`` join the leaf's tree instead,
and the three that collide with a leaf sub-verb carry two shapes — the
workspace form, which resolves the workspace the way every other agent verb
does, and the explicit form, which names its inputs on the command line and is
what arm C and an operator type.

The positional selects the form. These pin the workspace half: that it parses
with nothing after the verb, and that it reaches
:mod:`ai_rfc.server.core` rather than a second implementation.
"""

import json
from pathlib import Path

import pytest

from ai_rfc import cli as root_cli
from ai_rfc.server import cli as parity_cli
from ai_rfc.server import tools

pytestmark = pytest.mark.unit

#: A structure body worth declaring: the render changes with it, so a twin
#: over it is not degenerate.
_FIELDS = {
    "kind": "record",
    "title": "Message header",
    "section": "4",
    "fields": [{"name": "version", "type": "uint8", "claim": "t:1.1"}],
}

#: The workspace form of each folded verb, as an operator types it. Every one
#: of these parses with no positional and no required flag; ``commit``'s
#: ``-m`` is required because the core needs a message, not because a path
#: does.
WORKSPACE_FORM = [
    ["draft", "commit", "-m", "prose"],
    ["draft", "build"],
    ["draft", "lint"],
    ["draft", "render"],
]


@pytest.mark.parametrize("argv", WORKSPACE_FORM, ids=lambda a: " ".join(a[:2]))
def test_the_workspace_form_parses_with_no_path(argv):
    """The fold's whole point: these four take the workspace from the context.

    Before it, ``draft build`` demanded a ``draftrepo`` and an ``--out``,
    ``draft render`` a manifest, and ``draft commit`` did not exist at all.
    """
    root_cli.build_parser().parse_args(argv)


def test_draft_render_reaches_one_core(make_workspace, capsys):
    """The grouped verb prints the core's string, byte for byte.

    ``print(..., end="")`` rather than the JSON every other verb emits: the
    render *is* the payload, and ``test_draft_render_parity`` asserts the tool
    arm's output does not start with a quote.
    """
    build, use = make_workspace
    root_arm, parity_arm = build("root-arm"), build("parity-arm")

    use(root_arm)
    tools.ai_rfc_structure_upsert("header", _FIELDS)
    assert root_cli.main(["draft", "render"]) == 0
    from_root = capsys.readouterr().out

    use(parity_arm)
    tools.ai_rfc_structure_upsert("header", _FIELDS)
    assert parity_cli.main(["draft-render"]) == 0
    from_parity = capsys.readouterr().out

    assert from_root == from_parity
    assert not from_root.lstrip().startswith('"')


def test_draft_commit_reaches_one_core(make_workspace, capsys, monkeypatch):
    """A commit is a side effect, so the twin compares the tree as well.

    The author and committer dates are pinned: the two arms commit a moment
    apart, and the payload carries the sha.
    """
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-02T00:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-02T00:00:00+00:00")
    build, use = make_workspace
    root_arm, parity_arm = build("root-arm"), build("parity-arm")
    for root in (root_arm, parity_arm):
        prose = root / "draft" / "draft-test-spec.md"
        prose.write_text(prose.read_text() + "\nMore prose.\n")

    use(root_arm)
    assert root_cli.main(["draft", "commit", "-m", "more prose"]) == 0
    from_root = json.loads(capsys.readouterr().out)

    use(parity_arm)
    assert parity_cli.main(["draft-commit", "-m", "more prose"]) == 0
    from_parity = json.loads(capsys.readouterr().out)

    assert from_root == from_parity


def test_draft_lint_reaches_one_core(make_workspace, capsys):
    """``--committed`` is the flag the core takes; the default is the worktree.

    Not the leaf's ``--worktree``, whose default is the opposite: a bare
    ``ai-rfc draft lint`` must measure the uncommitted file, because that is
    what ``ai_rfc_draft_lint()`` does and what the twin compares against.
    """
    build, use = make_workspace
    root_arm, parity_arm = build("root-arm"), build("parity-arm")

    use(root_arm)
    assert root_cli.main(["draft", "lint"]) == 0
    from_root = json.loads(capsys.readouterr().out)

    use(parity_arm)
    assert parity_cli.main(["draft-lint"]) == 0
    from_parity = json.loads(capsys.readouterr().out)

    assert from_root["metrics"] == from_parity["metrics"]
    assert from_root["findings"] == from_parity["findings"]


def test_draft_build_without_a_toolchain_refuses_through_the_core(
    make_workspace, capsys, monkeypatch
):
    """The refusal is the core's, which is what proves the dispatch arrives.

    A real build needs a provisioned toolchain, so the reachable half is the
    guard: ``draft_build`` raises ``CoreError`` when ``AI_RFC_TOOLCHAIN`` is
    unset, ``perform`` reports it and returns 1. The leaf form's own refusal
    is a ``BuildError`` with different text, so a dispatch that fell through
    to it would fail here rather than pass quietly.
    """
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    build, use = make_workspace
    use(build("root-arm"))

    assert root_cli.main(["draft", "build"]) == 1
    assert "toolchain provision" in capsys.readouterr().err


#: A flag from one form and the other form's argv, with the flag the refusal
#: must name. ``--strict`` is the reason this is refused rather than ignored:
#: an ignored ``--strict`` is a gate an author believed they had run.
CROSSED = [
    (["draft", "build", "--strict"], "--strict"),
    (["draft", "build", "--out", "/w/out"], "--out"),
    (["draft", "lint", "--ref", "main"], "--ref"),
    (["draft", "lint", "--manifest", "/w/m.yaml"], "--manifest"),
    (["draft", "render", "--out", "/w/out"], "--out"),
    (["draft", "build", "/w/repo"], "--out"),
    (["draft", "lint", "/w/repo", "--out", "/w/out", "--committed"], "--committed"),
]


@pytest.mark.parametrize(
    ("argv", "named"), CROSSED, ids=[f"{a[1]}{f}" for a, f in CROSSED]
)
def test_a_flag_from_the_other_form_is_a_usage_error(argv, named, capsys):
    """2, and the flag named — not silently ignored, and not exit 1.

    2 belongs to argparse and means the invocation was wrong, which is what
    this is; routing it through ``parser.error`` rather than a diagnostic is
    what keeps that true and prints the usage line beneath it.
    """
    with pytest.raises(SystemExit) as excinfo:
        root_cli.main(argv)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert named in err
    assert "usage: ai-rfc draft" in err


def test_the_explicit_form_still_names_its_own_inputs(make_workspace, tmp_path: Path):
    """The fold added a form; it did not replace the one arm C and operators type.

    ``render MANIFEST`` is the shape ``docs/parity.md`` points an operator at,
    and it must keep working with no workspace resolvable at all — which is
    what the missing ``AI_RFC_CONFIG`` here asserts.
    """
    build, _ = make_workspace
    workspace = build("explicit")
    out = tmp_path / "out"

    assert root_cli.main(["draft", "render", str(workspace / "manifest.yaml")]) == 0
    assert (
        root_cli.main(["draft", "lint", str(workspace / "draft"), "--out", str(out)])
        == 0
    )
    assert (out / "lint-report.json").is_file()
