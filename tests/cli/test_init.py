"""`ai-rfc init`: a recon.yaml becomes a workspace.

A pinned clone, a scaffolded draft, empty registers and a sealed config, all
digested so the workspace verifies against what init recorded.
"""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from ai_rfc import cli
from ai_rfc.config import load_config
from ai_rfc.lifecycle.workspace import Layout, verify_digest
from ai_rfc.server.testing import git

#: Every key the skeleton's frontmatter declares. A scaffold that produced a
#: key outside this set would be writing Internet-Draft metadata nobody
#: configured, which is what the substitution tests below are guarding.
SKELETON_FRONTMATTER_KEYS = {
    "abbrev",
    "area",
    "author",
    "category",
    "docname",
    "informative",
    "ipr",
    "keyword",
    "normative",
    "pi",
    "smart_quotes",
    "stand_alone",
    "title",
    "workgroup",
}


@pytest.fixture(autouse=True)
def _experiments_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every defaulted path resolves under the test's own tree, never $HOME."""
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))


def _config(tmp_path: Path, source_repo: Path, extra: str = "") -> Path:
    path = tmp_path / "recon.yaml"
    path.write_text(
        "name: fixture\n"
        f"workspace: {tmp_path / 'ws'}\n"
        "source:\n"
        f"  repo: {source_repo}\n"
        "  host: none\n"
        "  pin: main\n"
        "draft:\n"
        "  name: draft-test-fixture\n" + extra
    )
    return path


def _frontmatter(body: str) -> dict:
    """The scaffolded draft's YAML frontmatter, parsed as the build parses it.

    kramdown-rfc opens the document with ``---`` and closes the frontmatter at
    the first section marker, so this reads exactly the region the toolchain
    hands to a YAML parser. Asserting against ``yaml.safe_load`` rather than
    against substrings is the point: a value that merely *looks* right in the
    text can still have restructured the document.

    Args:
        body: The scaffolded draft's full text.

    Returns:
        The frontmatter mapping.
    """
    assert body.startswith("---\n"), body[:40]
    return yaml.safe_load(body[len("---\n") : body.index("\n--- abstract")])


def _scaffold_with(tmp_path, source_repo, template_repo, extra):
    """Run ``ai-rfc init`` with extra ``draft:`` keys and return the draft body.

    Args:
        tmp_path: The test's tree.
        source_repo: The stand-in implementation repository.
        template_repo: The ``(url, commit)`` of the draft template.
        extra: Lines appended inside the config's ``draft:`` block.

    Returns:
        The scaffolded draft's text.
    """
    template, commit = template_repo
    config_path = _config(tmp_path, source_repo, extra=extra)
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    return (Layout(tmp_path / "ws").draft / "draft-test-fixture.md").read_text()


def test_init_builds_the_workspace_and_seals_the_config(
    tmp_path, source_repo, template_repo, capsys
):
    template, commit = template_repo
    config_path = _config(tmp_path, source_repo)
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    ws = Layout(tmp_path / "ws")
    head = git(source_repo, "rev-parse", "HEAD")
    assert git(ws.clone, "rev-parse", "HEAD") == head
    assert (ws.draft / "draft-test-fixture.md").exists() and (
        ws.draft / "Makefile"
    ).exists()
    assert ws.manifest.read_text().startswith("rfc: FIXTURE-RECON\n")
    assert (
        ws.questions.read_text() == "questions: {}\n"
        and ws.revisions.read_text() == "revisions: {}\n"
    )
    sealed = load_config(ws.config)
    assert sealed.name == "fixture" and sealed.source.pin == "main"
    # A production init writes the workspace the config already named, so no
    # drift line appears when the same file is passed to a later verb.
    assert sealed.workspace == ws.root
    record = json.loads(ws.init_record.read_text())
    assert record["resolved_pin"] == head and record["forge_snapshot"] is None
    assert (
        record["config_sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    )
    assert record["window"] is None and record["references"] == []
    assert verify_digest(ws.root) == []
    out = capsys.readouterr().out
    assert str(ws.root) in out and "ai-rfc run --config" in out


def test_init_refuses_an_existing_workspace(
    tmp_path, source_repo, template_repo, capsys
):
    template, commit = template_repo
    config_path = _config(tmp_path, source_repo)
    argv = [
        "init",
        "--config",
        str(config_path),
        "--template",
        template,
        "--template-commit",
        commit,
    ]
    assert cli.main(argv) == 0
    assert cli.main(argv) == 1
    assert "exists" in capsys.readouterr().err


def test_init_removes_the_workspace_it_half_built(
    tmp_path, source_repo, template_repo, capsys
):
    """A failed acquisition must leave nothing for the retry to trip over.

    ``init`` creates the root before it clones, so without cleanup a bad pin or
    an unreachable source leaves a directory the next attempt refuses as an
    already-initialised workspace, recoverable only with ``rm -rf``.
    """
    template, commit = template_repo
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    config_path = _config(tmp_path, not_a_repo)
    argv = [
        "init",
        "--config",
        str(config_path),
        "--template",
        template,
        "--template-commit",
        commit,
    ]
    assert cli.main(argv) == 1
    assert "cloning" in capsys.readouterr().err
    assert not (tmp_path / "ws").exists()

    assert cli.main(argv) == 1
    # The refusal by name: a bare "exists" would pass on git's own "does not
    # exist" and so would say nothing about the path this test is guarding.
    assert "initialised once" not in capsys.readouterr().err


def test_init_leaves_a_directory_it_did_not_create(
    tmp_path, source_repo, template_repo
):
    """The cleanup must never reach a tree that was there before this call.

    The refusal path exists because an operator may point ``init`` at a real
    directory, so only a root this invocation created may be removed.
    """
    template, commit = template_repo
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    config_path = _config(tmp_path, not_a_repo)
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 1
    )
    assert workspace.is_dir()


def test_init_seals_the_references_the_skeleton_itself_cites(
    tmp_path, source_repo, template_repo, toolchain_record
):
    """The scaffolded draft cites BCP 14 whether or not the config names it.

    A workspace's own refcache overrides the toolchain's shared one at build
    time, so a cache holding only the declared references cannot build the very
    draft ``init`` just scaffolded — the skeleton's ``{::boilerplate
    bcp14-tagged}`` pulls in RFC2119 and RFC8174 without declaring them.
    """
    template, commit = template_repo
    config_path = _config(
        tmp_path,
        source_repo,
        extra=f"references: [RFC9000]\ntoolchain: {toolchain_record}\n",
    )
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    ws = Layout(tmp_path / "ws")
    assert sorted(p.name for p in ws.refcache.iterdir()) == [
        "reference.RFC.2119.xml",
        "reference.RFC.8174.xml",
        "reference.RFC.9000.xml",
    ]
    # What the operator declared and what the workspace had to seal are two
    # different facts, and later rows read the first one.
    assert json.loads(ws.init_record.read_text())["references"] == ["RFC9000"]
    assert (ws.root / "references.yaml").read_text() == "references:\n- RFC9000\n"


def test_init_resolves_a_sha_pin_and_records_the_window(
    tmp_path, source_repo, template_repo
):
    template, commit = template_repo
    first = git(source_repo, "rev-list", "--max-parents=0", "HEAD")
    config_path = _config(tmp_path, source_repo, extra="window: [1, 1]\n")
    text = config_path.read_text().replace("pin: main", f"pin: {first}")
    config_path.write_text(text)
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    ws = Layout(tmp_path / "ws")
    assert git(ws.clone, "rev-parse", "HEAD") == first
    assert json.loads(ws.init_record.read_text())["window"] == [1, 1]


def test_init_with_references_needs_a_toolchain_and_seals_them(
    tmp_path, source_repo, template_repo, toolchain_record, capsys
):
    template, commit = template_repo
    config_path = _config(tmp_path, source_repo, extra="references: [RFC9000]\n")
    argv = [
        "init",
        "--config",
        str(config_path),
        "--template",
        template,
        "--template-commit",
        commit,
    ]
    assert cli.main(argv) == 1
    # Specific to the refusal: a bare "toolchain" also matches the defaulted
    # tools/toolchain.json path inside the same message, so it discriminates
    # nothing and the exit code would be carrying the whole assertion.
    assert "no toolchain record exists" in capsys.readouterr().err
    config_path.write_text(config_path.read_text() + f"toolchain: {toolchain_record}\n")
    assert cli.main(argv) == 0
    ws = Layout(tmp_path / "ws")
    assert (ws.refcache / "reference.RFC.9000.xml").exists()
    assert json.loads(ws.init_record.read_text())["references"] == ["RFC9000"]


def test_init_seeds_the_draft_from_the_substituted_skeleton(
    tmp_path, source_repo, template_repo
):
    """The scaffolded draft is the skeleton with this config's values in it.

    Every other assertion here stops at the file existing, which says nothing
    about where the skeleton was read from: the substrate reads it by a path
    built from its own module location, so moving the file leaves the constant
    resolvable and the read failing. Naming what the substitution had to put
    in the body is what makes that break visible.
    """
    template, commit = template_repo
    config_path = _config(tmp_path, source_repo)
    assert (
        cli.main(
            [
                "init",
                "--config",
                str(config_path),
                "--template",
                template,
                "--template-commit",
                commit,
            ]
        )
        == 0
    )
    body = (Layout(tmp_path / "ws").draft / "draft-test-fixture.md").read_text()
    front = _frontmatter(body)
    assert front["title"] == "fixture: A Reconstructed Specification"
    assert front["abbrev"] == "fixture Reconstructed"
    assert front["docname"] == "draft-test-fixture-latest"
    assert front["informative"]["SOURCE"]["title"].startswith(
        "The fixture implementation, as pinned"
    )
    assert "reconstructs the specification of fixture" in body
    # No placeholder survives: string.Template leaves a `$` behind only for a
    # `$$` escape, and the skeleton has none, so one here is an unsubstituted
    # slot that would reach a draft build as literal text. This fixture's own
    # values carry no `$`; that a `$` *inside a value* also survives is what
    # test_a_title_holding_a_dollar_is_not_re_substituted covers.
    assert "$" not in body


def test_a_title_holding_a_quote_round_trips_through_the_frontmatter(
    tmp_path, source_repo, template_repo
):
    """A title is a string, and the frontmatter must carry it back as one.

    A double quote is ordinary in a protocol name and needs no attacker: left
    unescaped inside the skeleton's ``title: "$title"`` it closes the scalar
    early, and the draft build then fails pointing at the generated file
    rather than at the config the operator actually wrote.
    """
    title = 'The "Foo" Protocol'
    body = _scaffold_with(
        tmp_path, source_repo, template_repo, "  title: 'The \"Foo\" Protocol'\n"
    )

    assert _frontmatter(body)["title"] == title


@pytest.mark.parametrize(
    ("line", "title"),
    [
        # Closes the skeleton's own quote first, so every following line is
        # read as another frontmatter key: this is the forging payload. The
        # skeleton declares `category` and `ipr` further down and PyYAML takes
        # the last of a duplicate, so these two are overridden right back —
        # ordering, not escaping, is all that stops them.
        (
            '  title: "ok\\"\\ncategory: std\\nipr: none"\n',
            'ok"\ncategory: std\nipr: none',
        ),
        # A key the skeleton never declares has no later duplicate to undo it,
        # so it lands in full. `obsoletes` and `submissiontype` are what an
        # Internet-Draft is filed as.
        (
            '  title: "ok\\"\\nobsoletes: 9999\\nsubmissiontype: IETF"\n',
            'ok"\nobsoletes: 9999\nsubmissiontype: IETF',
        ),
        # Without the quote the newline stays inside the scalar and YAML folds
        # it, so the title silently loses its line breaks instead.
        (
            '  title: "ok\\ncategory: std\\nipr: none"\n',
            "ok\ncategory: std\nipr: none",
        ),
    ],
    ids=["forges-duplicate-keys", "forges-novel-keys", "folds-value"],
)
def test_a_title_holding_a_newline_forges_no_frontmatter(
    tmp_path, source_repo, template_repo, line, title
):
    """A title is one value; it may not become three.

    Substituted raw, a newline inside the value ends the ``title:`` line and
    every following line is read as another frontmatter key — so the config's
    title field silently decides the draft's ``category`` and ``ipr``, which
    are what an Internet-Draft is published as.
    """
    front = _frontmatter(_scaffold_with(tmp_path, source_repo, template_repo, line))

    assert front["title"] == title
    assert set(front) == SKELETON_FRONTMATTER_KEYS
    # Key-set equality alone would pass a value the title overrode, and these
    # two are the ones worth overriding.
    assert front["category"] == "info" and front["ipr"] == "trust200902"


def test_an_abbrev_cannot_rewrite_the_title(tmp_path, source_repo, template_repo):
    """Ordering is not a defence, and ``abbrev`` is the proof.

    ``abbrev:`` sits below ``title:`` in the skeleton, so a key forged from it
    is the *last* of its duplicates and PyYAML takes it — the draft's title
    then comes from the abbrev field. Every substituted value has to be
    emitted as a scalar; escaping only the first one moves the hole.
    """
    abbrev = 'ok"\ntitle: FORGED\ncategory: std'
    front = _frontmatter(
        _scaffold_with(
            tmp_path,
            source_repo,
            template_repo,
            '  abbrev: "ok\\"\\ntitle: FORGED\\ncategory: std"\n',
        )
    )

    assert front["abbrev"] == abbrev
    assert front["title"] == "fixture: A Reconstructed Specification"
    assert front["category"] == "info"
    assert set(front) == SKELETON_FRONTMATTER_KEYS


def test_a_title_holding_a_dollar_is_not_re_substituted(
    tmp_path, source_repo, template_repo
):
    """Substitution is single-pass, and the frontmatter must prove it.

    A value naming a placeholder must reach the draft as itself; a second pass
    would expand it and a stray ``$`` would raise instead.
    """
    body = _scaffold_with(
        tmp_path, source_repo, template_repo, "  title: 'Costs $target and $5'\n"
    )

    assert _frontmatter(body)["title"] == "Costs $target and $5"
