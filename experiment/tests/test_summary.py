"""The per-cluster record, tested against hand-built artifacts.

Every section here must degrade rather than raise: the record is written from
inside a loop that may already be six hours into a sweep.
"""

import subprocess

import yaml


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _draft_repo(tmp_path, revisions, *, scaffold=True):
    """A draft repo with one commit and tag per revision, oldest first.

    A real draft is seeded from the auto-i-d template before any revision, so
    the first tag has a parent to diff against; ``scaffold=False`` models the
    degenerate repo whose first revision is the root commit.
    """
    repo = tmp_path / "draft"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    if scaffold:
        (repo / "README").write_text("scaffold\n")
        _git(repo, "add", "README")
        _git(repo, "commit", "-qm", "scaffold from auto-i-d-template")
    doc = repo / "draft-test.md"
    for number, body in enumerate(revisions, start=1):
        doc.write_text(body)
        _git(repo, "add", "draft-test.md")
        _git(repo, "commit", "-qm", f"revision {number}")
        _git(repo, "tag", f"draft-test-{number:02d}")
    return repo


def test_a_revision_is_found_by_cluster_id_not_by_digest(tmp_path):
    """Two revisions share a manifest digest when the manifest did not change."""
    from experiment.summary import revision_of

    (tmp_path / "revisions.yaml").write_text(
        yaml.safe_dump(
            {
                "revisions": {
                    "draft-test-01": {
                        "cluster_id": "c1",
                        "normative_change": True,
                        "note": "first",
                        "checkpoint_manifest_sha256": "same",
                    },
                    "draft-test-02": {
                        "cluster_id": "c2",
                        "normative_change": False,
                        "note": "second",
                        "checkpoint_manifest_sha256": "same",
                    },
                }
            }
        )
    )

    entry = revision_of(tmp_path, "c2")
    assert entry["tag"] == "draft-test-02"
    assert entry["note"] == "second" and entry["normative_change"] is False
    assert revision_of(tmp_path, "absent") is None


def test_a_missing_revisions_file_is_not_an_error(tmp_path):
    from experiment.summary import revision_of

    assert revision_of(tmp_path, "c1") is None


def test_the_citation_delta_compares_against_the_previous_tag_number(tmp_path):
    """Tags are a revision sequence, not cluster ordinals."""
    from experiment.summary import citation_delta

    _draft_repo(
        tmp_path,
        ["cites `ai_rfc:a`\n", "cites `ai_rfc:a` and `ai_rfc:b`\n"],
    )

    delta = citation_delta(tmp_path, "draft-test-02")
    assert delta["added"] == ["b"]
    assert delta["removed"] == []
    assert delta["prev_tag"] == "draft-test-01"
    assert delta["error"] is None

    # The first revision has no predecessor: everything it cites is added.
    first = citation_delta(tmp_path, "draft-test-01")
    assert first["prev_tag"] is None and first["added"] == ["a"]


def test_the_diffstat_reports_the_prose_change(tmp_path):
    from experiment.summary import diffstat

    _draft_repo(tmp_path, ["one\n", "one\ntwo\nthree\n"])

    assert diffstat(tmp_path, "draft-test-02") == {
        "files": 1,
        "insertions": 2,
        "deletions": 0,
    }


def test_the_first_revision_diffs_against_its_own_parent(tmp_path):
    """There is no tag before the first, but there is a scaffold commit."""
    from experiment.summary import diffstat

    _draft_repo(tmp_path, ["one\n"])

    assert diffstat(tmp_path, "draft-test-01") == {
        "files": 1,
        "insertions": 1,
        "deletions": 0,
    }


def test_a_first_revision_with_no_parent_falls_back_to_the_empty_tree(tmp_path):
    """A root-commit revision has no parent to name, and is still a diff."""
    from experiment.summary import diffstat

    _draft_repo(tmp_path, ["one\ntwo\n"], scaffold=False)

    assert diffstat(tmp_path, "draft-test-01") == {
        "files": 1,
        "insertions": 2,
        "deletions": 0,
    }


def test_a_missing_draft_repo_yields_no_diffstat(tmp_path):
    from experiment.summary import citation_delta, diffstat

    assert diffstat(tmp_path, "draft-test-01") is None
    assert citation_delta(tmp_path, "draft-test-01")["error"] is not None
