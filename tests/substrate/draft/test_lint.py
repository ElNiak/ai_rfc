"""``draft lint``: metrics and findings over one draft text."""

import json
from pathlib import Path

import pytest

from ai_rfc.draft.cli import main
from ai_rfc.draft.lint import MUST_FRACTION_CEILING, STUB_ABSTRACT_MARKER, lint
from ai_rfc.schema import load

from .conftest import _manifest_text, git

pytestmark = pytest.mark.unit

# No bare `---` closer: the skeleton and every draft this tool produces close
# their front matter with `--- abstract`. A fixture that adds one would exercise
# a path no real draft takes and hide a broken `_parts`.
FRONT = "---\n" 'title: "T"\n' "docname: draft-test-spec-latest\n" "{refs}"
STUB = (
    "This document reconstructs the specification of t from its\n"
    "implementation history. " + STUB_ABSTRACT_MARKER + ".\n"
)
WRITTEN = "T is a server that stores things and answers queries about them.\n"
MIDDLE = (
    "--- middle\n\n# Introduction\n\n{intro}\n\n# Conventions\n\n"
    "{{::boilerplate bcp14-tagged}}\n\n"
    "# Operation\n\n{body}\n\n# Security Considerations\n\nNone known.\n\n"
    "# IANA Considerations\n\nNone.\n"
)


def _draft(
    *,
    abstract=WRITTEN,
    intro="T stores things.",
    body="",
    refs="normative:\n  RFC9000:\n",
    back="--- back\n",
):
    return (
        FRONT.format(refs=refs)
        + "\n--- abstract\n\n"
        + abstract
        + "\n"
        + MIDDLE.format(intro=intro, body=body)
        + "\n"
        + back
    )


def test_a_skeleton_abstract_is_a_finding():
    report = lint(_draft(abstract=STUB))
    assert report.abstract["is_stub"] is True
    assert "abstract: still the skeleton stub" in report.findings
    assert lint(_draft()).abstract["is_stub"] is False


def test_a_wrapped_skeleton_abstract_is_still_a_finding():
    # A real draft is hard-wrapped by xml2rfc, and the marker's own space can
    # fall on a line break exactly as it does in the shipped MARK draft.
    wrapped_marker = STUB_ABSTRACT_MARKER.replace("as it stood", "as it\nstood")
    wrapped_stub = (
        "This document reconstructs the specification of t from its\n"
        "implementation history. " + wrapped_marker + ".\n"
    )
    report = lint(_draft(abstract=wrapped_stub))
    assert report.abstract["is_stub"] is True


def test_required_sections_are_checked_by_level_one_heading():
    text = _draft().replace("# IANA Considerations\n\nNone.\n", "")
    report = lint(text)
    assert report.sections["missing"] == ["IANA Considerations"]
    assert "section missing: IANA Considerations" in report.findings
    assert lint(_draft()).sections["missing"] == []


def test_a_heading_anchor_does_not_hide_the_section_or_its_narration():
    text = (
        _draft(intro="The cluster grew.")
        .replace("# Introduction\n", "# Introduction {#intro}\n")
        .replace("# IANA Considerations\n", "# IANA Considerations {#iana}\n")
    )
    report = lint(text)
    assert report.sections["missing"] == []
    assert any(entry["pattern"] == "cluster" for entry in report.narration)


def test_references_are_counted_from_the_front_matter():
    report = lint(
        _draft(
            refs=(
                "normative:\n  RFC9000:\ninformative:\n  MARK:\n"
                "    title: The paper\n    date: 2019\n"
            )
        )
    )
    assert report.references == {"normative": 1, "informative": 1, "inline": 1}
    empty = lint(_draft(refs="normative:\ninformative:\n"))
    assert empty.references == {"normative": 0, "informative": 0, "inline": 0}
    assert (
        "references: none declared (normative and informative are both empty)"
        in empty.findings
    )


def test_keywords_are_counted_outside_fences_and_the_boilerplate_line():
    body = (
        "The server MUST answer. It MUST NOT lie. Clients SHOULD retry and MAY log.\n\n"
        "~~~\nMUST inside artwork does not count\n~~~\n"
    )
    report = lint(_draft(body=body))
    assert report.keywords["histogram"] == {
        "MUST": 1,
        "MUST NOT": 1,
        "SHOULD": 1,
        "MAY": 1,
    }
    assert report.keywords["total"] == 4 and report.keywords["must_fraction"] == 0.5


def test_a_must_monoculture_is_a_finding_only_over_twenty_keywords():
    body = " ".join(["It MUST run."] * 19)
    assert not any(f.startswith("keywords:") for f in lint(_draft(body=body)).findings)
    body = " ".join(["It MUST run."] * 21)
    report = lint(_draft(body=body))
    assert report.keywords["must_fraction"] == 1.0
    assert any(
        f.startswith(f"keywords: MUST fraction 1.00 exceeds {MUST_FRACTION_CEILING}")
        for f in report.findings
    )


def test_figures_need_a_citation_within_three_lines_of_the_closing_fence():
    cited = (
        '~~~\n+---+\n| A |\n+---+\n~~~\n{: #fig-a title="A"}\n\n'
        "A holds the thing. `ai_rfc:spec:1.1`\n"
    )
    uncited = (
        "~~~\n+---+\n| B |\n+---+\n~~~\n\nB is drawn above.\n\n"
        "More prose.\n\nEven more.\n"
    )
    report = lint(_draft(body=cited + "\n" + uncited))
    assert report.blocks["figures"] == 2
    assert len(report.blocks["figures_without_caption_citation"]) == 1
    line = report.blocks["figures_without_caption_citation"][0]["line"]
    assert any(
        f == f"figure at line {line}: no citation within 3 lines of its closing fence"
        for f in report.findings
    )


def test_a_citation_exactly_three_lines_after_the_fence_counts_but_four_does_not():
    within = (
        "~~~\n+---+\n| C |\n+---+\n~~~\n" "line1\nline2\nline3 with `ai_rfc:spec:1.1`\n"
    )
    beyond = (
        "~~~\n+---+\n| D |\n+---+\n~~~\n"
        "line1\nline2\nline3\nline4 with `ai_rfc:spec:1.1`\n"
    )
    assert lint(_draft(body=within)).blocks["figures_without_caption_citation"] == []
    beyond_report = lint(_draft(body=beyond))
    assert len(beyond_report.blocks["figures_without_caption_citation"]) == 1


def test_tables_are_counted_by_their_rule_row():
    # `_TABLE_RULE` requires three or more dashes per cell, so an alignment row
    # must be written `|:---:|`, not `|:-:|`. The floor is deliberate: a shorter
    # run would also match a bare `---` thematic break.
    body = "| Field | Type |\n|---|---|\n| a | int |\n\n| X |\n|:---:|\n| 1 |\n"
    assert lint(_draft(body=body)).blocks["tables"] == 2


def test_a_figure_in_the_back_matter_reports_its_true_line_number():
    back = "--- back\n\n~~~\n+---+\n| Z |\n+---+\n~~~\n\nNo citation nearby at all.\n"
    text = _draft(back=back)
    report = lint(text)
    assert len(report.blocks["figures_without_caption_citation"]) == 1
    expected_line = text.splitlines().index("~~~") + 1
    assert report.blocks["figures_without_caption_citation"][0]["line"] == expected_line


def test_citations_are_measured_against_the_manifest(tmp_path):
    manifest_path = tmp_path / "m.yaml"
    manifest_path.write_text(_manifest_text(with_second_claim=True))
    manifest = load(manifest_path)
    body = (
        "It does the thing. `ai_rfc:spec:1.1` It is old. `a_rfc:spec:2.1` "
        "Unknown. `ai_rfc:spec:9.9`\n"
    )
    report = lint(_draft(body=body), manifest=manifest)
    assert report.citations["tokens"] == 2 and report.citations["legacy_tokens"] == 1
    assert report.citations["cited_unknown"] == ["spec:9.9"]
    assert report.citations["uncited"] == ["spec:2.1"]
    assert report.citations["cited_fraction"] == 0.5
    assert "citation spec:9.9: not in the manifest" in report.findings


def test_narration_is_detected_in_the_introduction_only():
    intro = (
        "The thirty-first cluster is a merge. Forty-five statements are "
        "added and four are withdrawn."
    )
    body = "Clients form a cluster of peers.\n"
    report = lint(_draft(intro=intro, body=body))
    patterns = [entry["pattern"] for entry in report.narration]
    assert "ordinal cluster" in patterns and "added/withdrawn count" in patterns
    assert all(entry["line"] < 20 for entry in report.narration)
    assert any(
        f.startswith("introduction: narrates the reconstruction (")
        for f in report.findings
    )
    assert lint(_draft(body=body)).narration == []


def test_the_generic_cluster_pattern_is_a_fallback_not_an_extra_entry():
    # "first ... cluster" always satisfies the bare `cluster` pattern too
    # (it requires the word "cluster"), so counting both would double-report
    # the same tell under two names.
    intro = "The first cluster was formed early."
    report = lint(_draft(intro=intro))
    assert [entry["pattern"] for entry in report.narration] == ["ordinal cluster"]


def test_the_narration_finding_counts_distinct_lines_not_pattern_matches():
    intro = "The first cluster was formed early.\nThe cluster grew over time."
    report = lint(_draft(intro=intro))
    assert len(report.narration) == 2
    line = report.narration[0]["line"]
    assert (
        f"introduction: narrates the reconstruction (2 line(s), e.g. line {line})"
        in report.findings
    )


def test_an_unloadable_manifest_is_a_finding_not_a_crash():
    report = lint(
        _draft(), manifest_error="level 'descriptive' is not one of MUST, ..."
    )
    assert (
        "manifest: unloadable (level 'descriptive' is not one of MUST, ...)"
        in report.findings
    )


def test_a_rewritten_abstract_with_the_stub_comment_still_present_is_not_a_stub():
    # A comment that explains the stub marker by quoting it verbatim must not
    # itself keep the abstract flagged once the real paragraph is rewritten —
    # kramdown-rfc drops the comment at render time, and the lint must
    # measure the abstract the same way.
    real_paragraph = "T answers queries about the things it stores for its clients."
    abstract = (
        real_paragraph
        + "\n\n{::comment}\nReplace the paragraph above once the system is "
        'understood. The sentence "' + STUB_ABSTRACT_MARKER + '" is how the '
        "lint recognises an unwritten abstract.\n{:/comment}\n"
    )
    report = lint(_draft(abstract=abstract))
    assert report.abstract["is_stub"] is False
    assert report.abstract["word_count"] == len(real_paragraph.split())


def test_the_figures_reference_examples_citation_is_within_the_window():
    # Mirrors plugins/ai-rfc/skills/ai-rfc-figures/references/figure-example.md:
    # the closing fence's next line is the `{: ...}` attribute, which counts
    # as the first of the three lines the lint's citation window checks, so
    # the caption and its citations must land within the next two.
    example = (
        "~~~\n"
        "+--------+   raw data   +--------+   evidence   +---------+\n"
        "| Client | -----------> | Server | -----------> | Storage |\n"
        "+--------+              +--------+              +---------+\n"
        "~~~\n"
        '{: #fig-overview title="Components of the system"}\n\n'
        "Clients submit raw data that the server stores as evidence. "
        "`ai_rfc:mark:arch.1` `ai_rfc:mark:store.2`\n"
    )
    report = lint(_draft(body=example))
    assert report.blocks["figures_without_caption_citation"] == []
    assert not any(f.startswith("figure at line") for f in report.findings)


@pytest.fixture
def lint_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "draft"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "draft-test-spec.md").write_text(_draft(abstract=STUB))
    git(repo, "add", "draft-test-spec.md")
    git(repo, "commit", "-m", "revision 00")
    git(repo, "tag", "draft-test-spec-00")
    (repo / "draft-test-spec.md").write_text(_draft())
    return repo


def test_cli_lint_reads_a_ref_by_default_and_the_worktree_on_request(
    lint_repo, tmp_path, capsys
):
    out = tmp_path / "out"
    assert main(["lint", str(lint_repo), "--out", str(out)]) == 0
    written = json.loads((out / "lint-report.json").read_text())
    assert written["abstract"]["is_stub"] is True and written["source"]["ref"] == "HEAD"
    assert "abstract: still the skeleton stub" in capsys.readouterr().err
    assert main(["lint", str(lint_repo), "--out", str(out), "--worktree"]) == 0
    written = json.loads((out / "lint-report.json").read_text())
    assert (
        written["abstract"]["is_stub"] is False
        and written["source"]["ref"] == "worktree"
    )


def test_cli_lint_strict_exits_three_and_the_report_is_byte_stable(
    lint_repo, tmp_path, capsys
):
    out = tmp_path / "out"
    assert main(["lint", str(lint_repo), "--out", str(out), "--strict"]) == 3
    first = (out / "lint-report.json").read_bytes()
    main(["lint", str(lint_repo), "--out", str(out), "--strict"])
    assert (out / "lint-report.json").read_bytes() == first
    capsys.readouterr()


def test_cli_lint_reports_a_malformed_manifest_as_a_finding_not_a_crash(
    lint_repo, tmp_path, capsys
):
    broken_manifest = tmp_path / "broken.yaml"
    broken_manifest.write_text("claims: [\n")
    out = tmp_path / "out"
    assert (
        main(
            [
                "lint",
                str(lint_repo),
                "--out",
                str(out),
                "--manifest",
                str(broken_manifest),
            ]
        )
        == 0
    )
    assert "manifest: unloadable (" in capsys.readouterr().err
