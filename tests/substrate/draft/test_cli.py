from pathlib import Path

import pytest

from ai_rfc.draft import cli
from ai_rfc.timeline.store import read_clusters

from .conftest import STRUCTURED_BLOCK, _manifest_text, git

pytestmark = pytest.mark.unit


def test_checkpoint_verb_writes_a_checkpoint(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path, capsys
):
    cluster_id = read_clusters(timeline_dir)[0]["id"]
    code = cli.main(
        [
            "checkpoint",
            str(manifest_path),
            "--timeline",
            str(timeline_dir),
            "--cluster",
            cluster_id,
            "--out",
            str(tmp_path / "checkpoints"),
        ]
    )
    assert code == 0
    assert (tmp_path / "checkpoints" / cluster_id / "checkpoint.json").exists()
    assert "checkpoint written" in capsys.readouterr().err


def test_checkpoint_verb_exits_one_on_unknown_cluster(
    manifest_path: Path, timeline_dir: Path, tmp_path: Path, capsys
):
    code = cli.main(
        [
            "checkpoint",
            str(manifest_path),
            "--timeline",
            str(timeline_dir),
            "--cluster",
            "c9999-pr-000000000000",
            "--out",
            str(tmp_path / "checkpoints"),
        ]
    )
    assert code == 1
    assert "error" in capsys.readouterr().err


def _gate_argv(workspace: dict[str, Path], out: Path, *extra: str) -> list[str]:
    return [
        "gate",
        str(workspace["repo"]),
        "--timeline",
        str(workspace["timeline"]),
        "--checkpoints",
        str(workspace["checkpoints"]),
        "--questions",
        str(workspace["questions"]),
        "--revisions",
        str(workspace["revisions"]),
        "--out",
        str(out),
        *extra,
    ]


def test_gate_verb_clean_writes_report_and_exits_zero(
    draft_workspace, tmp_path: Path, capsys
):
    out = tmp_path / "out"
    assert cli.main(_gate_argv(draft_workspace, out)) == 0
    assert (out / "gate-report.json").read_text() == '{\n  "findings": []\n}\n'
    assert "gate clean" in capsys.readouterr().err


def test_gate_verb_reports_findings_without_strict(
    draft_workspace, tmp_path: Path, capsys
):
    git(draft_workspace["repo"], "tag", "-d", "draft-test-spec-01")
    assert cli.main(_gate_argv(draft_workspace, tmp_path / "out")) == 0
    assert "finding:" in capsys.readouterr().err


def test_gate_verb_strict_exits_three_on_findings(draft_workspace, tmp_path: Path):
    git(draft_workspace["repo"], "tag", "-d", "draft-test-spec-01")
    assert cli.main(_gate_argv(draft_workspace, tmp_path / "out", "--strict")) == 3


def test_gate_verb_exits_one_on_missing_inputs(draft_workspace, tmp_path: Path, capsys):
    draft_workspace["revisions"].unlink()
    assert cli.main(_gate_argv(draft_workspace, tmp_path / "out")) == 1
    assert "error" in capsys.readouterr().err


def test_render_prints_the_blocks_and_does_not_fall_through_to_gate(tmp_path, capsys):
    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=True) + STRUCTURED_BLOCK)
    assert cli.main(["render", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "ai_rfc:struct:header begin" in printed
    assert "gate-report.json" not in printed


def test_render_can_also_write_the_file(tmp_path, capsys):
    from ai_rfc.draft.structures import STRUCTURES_FILE

    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=True) + STRUCTURED_BLOCK)
    out = tmp_path / "out"
    assert cli.main(["render", str(path), "--out", str(out)]) == 0
    capsys.readouterr()
    assert "ai_rfc:struct:header begin" in (out / STRUCTURES_FILE).read_text()


def test_render_of_a_structure_free_manifest_prints_nothing_and_succeeds(
    tmp_path, capsys
):
    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    assert cli.main(["render", str(path)]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_checkpoint_writes_a_consolidation_when_asked(tmp_path, timeline_dir, capsys):
    cluster_id = read_clusters(timeline_dir)[1]["id"]
    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=True) + STRUCTURED_BLOCK)
    checkpoints = tmp_path / "checkpoints"
    assert (
        cli.main(
            [
                "checkpoint",
                str(path),
                "--timeline",
                str(timeline_dir),
                "--cluster",
                cluster_id,
                "--out",
                str(checkpoints),
            ]
        )
        == 0
    )
    consolidations = tmp_path / "consolidations"
    assert (
        cli.main(
            [
                "checkpoint",
                str(path),
                "--timeline",
                str(timeline_dir),
                "--cluster",
                cluster_id,
                "--out",
                str(consolidations),
                "--consolidation",
                "1",
                "--base",
                str(checkpoints / cluster_id),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (consolidations / "01" / "checkpoint.json").is_file()


def test_consolidation_requires_a_base(tmp_path, timeline_dir, capsys):
    path = tmp_path / "m.yaml"
    path.write_text(_manifest_text(with_second_claim=False))
    with pytest.raises(SystemExit) as exit_:
        cli.main(
            [
                "checkpoint",
                str(path),
                "--timeline",
                str(timeline_dir),
                "--cluster",
                read_clusters(timeline_dir)[0]["id"],
                "--out",
                str(tmp_path / "c"),
                "--consolidation",
                "1",
            ]
        )
    assert exit_.value.code == 2
    assert "--base" in capsys.readouterr().err
