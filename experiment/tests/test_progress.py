"""The progress model and its rendering, tested apart from the loop.

Separated from ``test_per_cluster`` for the reason the module is: the timeline
reader is the part that can silently disagree with ``views.emit``, and it is
worth exercising without a campaign fixture in the way.
"""

import json

import pytest

from experiment import ExperimentError
from experiment.progress import _bar, _duration, _log_safe, cluster_span, describe


def test_window_progress_counts_only_the_work_this_run_can_do(tmp_path, monkeypatch):
    """Pre-seeded clusters are a baseline's work, not this run's.

    Counting them would report progress the run did not make.
    """
    import experiment.progress as progress

    rows = [{"ordinal": n, "id": f"c{n}"} for n in (1, 2, 3, 4)]
    artifacts = {
        "c1": {"artifacts": False, "pre_seeded": True},
        "c2": {"artifacts": True, "pre_seeded": False},
        "c3": {"artifacts": False, "pre_seeded": False},
        "c4": {"artifacts": True, "pre_seeded": False},
    }
    monkeypatch.setattr(progress, "window_clusters", lambda _ws: rows)
    monkeypatch.setattr(
        progress, "cluster_artifacts", lambda _ws, row: artifacts[row["id"]]
    )

    row, selected, position, done, total = progress.window_progress(tmp_path)

    assert row["id"] == "c3"
    # Returned so the caller need not read the same record a second time.
    assert selected is artifacts["c3"]
    # c1 is pre-seeded: neither numerator nor denominator.
    assert (position, done, total) == (2, 2, 3)
    # c4 is finished but sits after c3, so position is not done + 1.
    assert position != done + 1


def test_an_exhausted_window_reports_no_row_and_no_artifacts(tmp_path, monkeypatch):
    """The done case still has to carry usable counts for the final line."""
    import experiment.progress as progress

    monkeypatch.setattr(
        progress, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    monkeypatch.setattr(
        progress,
        "cluster_artifacts",
        lambda _ws, _row: {"artifacts": True, "pre_seeded": False},
    )

    assert progress.window_progress(tmp_path) == (None, None, 0, 1, 1)


def test_a_wholly_pre_seeded_window_counts_nothing(tmp_path, monkeypatch):
    """A resealed workspace can leave this run with no work at all."""
    import experiment.progress as progress

    monkeypatch.setattr(
        progress, "window_clusters", lambda _ws: [{"ordinal": 1, "id": "c1"}]
    )
    monkeypatch.setattr(
        progress,
        "cluster_artifacts",
        lambda _ws, _row: {"artifacts": False, "pre_seeded": True},
    )

    assert progress.window_progress(tmp_path) == (None, None, 0, 0, 0)


def _write_members(workspace, rows):
    timeline = workspace / "timeline"
    timeline.mkdir(parents=True, exist_ok=True)
    (timeline / "members.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )


def test_a_pr_span_ends_at_its_anchor_merge(tmp_path):
    _write_members(
        tmp_path,
        [
            {"cluster_id": "c1", "sha": "aaaaaaa111", "position": 0, "role": "branch"},
            {"cluster_id": "c1", "sha": "bbbbbbb222", "position": 1, "role": "anchor"},
        ],
    )
    cluster = {
        "id": "c1",
        "kind": "pr",
        "anchor_sha": "bbbbbbb222",
        "spine_prev_sha": "0000000999",
    }

    assert cluster_span(tmp_path, cluster) == "0000000..bbbbbbb"


def test_an_epoch_span_ends_past_its_anchor(tmp_path):
    """An epoch's anchor is its FIRST member, so ending there would drop the rest."""
    _write_members(
        tmp_path,
        [
            {"cluster_id": "c2", "sha": "ccccccc333", "position": 0, "role": "spine"},
            {"cluster_id": "c2", "sha": "ddddddd444", "position": 1, "role": "spine"},
        ],
    )
    cluster = {
        "id": "c2",
        "kind": "epoch",
        "anchor_sha": "ccccccc333",
        "spine_prev_sha": None,
    }

    # spine_prev_sha is None at the root of the timeline.
    assert cluster_span(tmp_path, cluster) == "root..ddddddd"


def test_a_cluster_with_no_members_on_disk_has_no_span(tmp_path):
    """The stubbed rows the loop's other tests use are not in members.jsonl."""
    assert cluster_span(tmp_path, {"id": "c1"}) is None


def test_a_pr_whose_members_contradict_its_anchor_is_refused(tmp_path):
    """Disagreement here means the printed span would not match span.diff."""
    _write_members(
        tmp_path,
        [{"cluster_id": "c3", "sha": "eeeeeee555", "position": 0, "role": "anchor"}],
    )
    cluster = {
        "id": "c3",
        "kind": "pr",
        "anchor_sha": "fffffff666",
        "spine_prev_sha": None,
    }

    with pytest.raises(ExperimentError, match="anchor merge"):
        cluster_span(tmp_path, cluster)


def test_the_span_agrees_with_the_rule_views_emit_uses(tmp_path):
    """Pins the rule, not the data.

    ``views.emit`` lives in a different package outside this submodule, so a
    change to its rule would never fail a test here. Its rule is two lines
    (``emit.py``: ``base = cluster["spine_prev_sha"] or EMPTY_TREE`` and
    ``span = _span_diff(repo, base, member_shas[-1])``), restated here so the
    two can be compared without importing across the boundary.
    """
    empty_tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    members = {
        "c1": [  # a PR: the anchor merge is the LAST member
            {"cluster_id": "c1", "sha": "1111111aaa", "position": 0, "role": "branch"},
            {"cluster_id": "c1", "sha": "2222222bbb", "position": 1, "role": "anchor"},
        ],
        "c2": [  # an epoch: the anchor is the FIRST member
            {"cluster_id": "c2", "sha": "3333333ccc", "position": 0, "role": "spine"},
            {"cluster_id": "c2", "sha": "4444444ddd", "position": 1, "role": "spine"},
        ],
    }
    _write_members(tmp_path, members["c1"] + members["c2"])
    clusters = [
        {
            "id": "c1",
            "kind": "pr",
            "anchor_sha": "2222222bbb",
            "spine_prev_sha": "0000000zzz",
        },
        {
            "id": "c2",
            "kind": "epoch",
            "anchor_sha": "3333333ccc",
            "spine_prev_sha": "2222222bbb",
        },
    ]

    for cluster in clusters:
        base = cluster["spine_prev_sha"] or empty_tree
        head = members[cluster["id"]][-1]["sha"]  # emit's file-order [-1]
        expected = f"{'root' if base == empty_tree else base[:7]}..{head[:7]}"
        assert cluster_span(tmp_path, cluster) == expected, cluster["id"]
        # The property that makes the rule non-obvious, asserted directly.
        if cluster["kind"] == "epoch":
            assert head != cluster["anchor_sha"]
        else:
            assert head == cluster["anchor_sha"]


def test_a_damaged_members_file_does_not_raise(tmp_path):
    """A progress line must never be the thing that ends a run.

    Each of these escaped an earlier guard that caught only OSError: a kill
    truncating the last line mid-write raises JSONDecodeError, undecodable
    bytes raise UnicodeDecodeError, and a row missing a field raises KeyError
    or TypeError. All five are survivable; none is worth losing a run over.
    """
    timeline = tmp_path / "timeline"
    timeline.mkdir(parents=True)
    good = {"cluster_id": "c1", "sha": "aaaaaaa111", "position": 0, "role": "spine"}
    path = timeline / "members.jsonl"

    # A line truncated mid-write, as a kill leaves it.
    path.write_text(json.dumps(good) + "\n" + '{"cluster_id": "c1", "sh')
    assert cluster_span(tmp_path, {"id": "c1", "kind": "epoch"}) == "root..aaaaaaa"

    # Bytes that are not UTF-8 at all.
    path.write_bytes(json.dumps(good).encode() + b"\n\xff\xfe rubbish\n")
    assert cluster_span(tmp_path, {"id": "c1", "kind": "epoch"}) == "root..aaaaaaa"

    # Rows missing the fields the selection needs, or holding the wrong type.
    for broken in (
        {"cluster_id": "c1", "sha": "z"},
        {"cluster_id": "c1", "sha": None, "position": 9},
    ):
        path.write_text(json.dumps(good) + "\n" + json.dumps(broken) + "\n")
        assert cluster_span(tmp_path, {"id": "c1", "kind": "epoch"}) == "root..aaaaaaa"

    # Nothing usable left at all is "no span", not an exception.
    path.write_text("{ broken\n")
    assert cluster_span(tmp_path, {"id": "c1", "kind": "epoch"}) is None


def test_the_bar_fills_with_finished_clusters():
    assert _bar(0, 10) == "[----------]"
    assert _bar(3, 10) == "[###-------]"
    assert _bar(10, 10) == "[##########]"
    # A window with nothing countable must not divide by zero.
    assert _bar(0, 0) == "[----------]"


def test_durations_read_as_wall_clock():
    assert _duration(41 * 60) == "41m"
    assert _duration(8 * 3600) == "8h00m"
    assert _duration(90 * 60) == "1h30m"


def test_a_cluster_is_described_from_whatever_the_row_carries():
    full = {
        "id": "c0043-pr-9f3e21ab77c1",
        "kind": "pr",
        "member_count": 7,
        "title": "Add BGP path attributes (#412)",
    }
    assert describe(full, "c84a5f0..9f3e21a") == (
        "c0043-pr-9f3e21ab77c1 - pr, 7 commits - c84a5f0..9f3e21a"
        ' - "Add BGP path attributes (#412)"'
    )

    epoch = {
        "id": "c0044-epoch-3a91",
        "kind": "epoch",
        "member_count": 12,
        "title": "Bump dependency pins",
    }
    assert describe(epoch, "9f3e21a..7c02be1") == (
        "c0044-epoch-3a91 - epoch, 12 commits - 9f3e21a..7c02be1"
        ' - from "Bump dependency pins"'
    )

    # The stubbed rows the loop's other tests use carry nothing but id/ordinal.
    assert describe({"id": "c1"}, None) == "c1"


def test_a_single_commit_cluster_is_not_described_as_commits():
    """forge_squash clusters really do hold exactly one member."""
    row = {"id": "c1", "kind": "pr", "member_count": 1}
    assert describe(row, None) == "c1 - pr, 1 commit"


def test_a_zero_member_count_is_still_a_count():
    """`if count` would drop it; only absence should drop it."""
    row = {"id": "c1", "kind": "epoch", "member_count": 0}
    assert describe(row, None) == "c1 - epoch, 0 commits"


def test_a_long_subject_is_truncated_at_the_limit():
    row = {"id": "c1", "kind": "pr", "member_count": 1, "title": "x" * 90}
    # Pinned exactly: "x" * 57 + "..." is a substring of every longer run, so a
    # containment assertion would pass for any limit from 57 to 60.
    assert describe(row, None) == 'c1 - pr, 1 commit - "' + "x" * 57 + '..."'


def test_a_subject_at_the_limit_is_left_alone():
    row = {"id": "c1", "title": "y" * 60}
    assert describe(row, None) == 'c1 - "' + "y" * 60 + '"'


def test_a_subject_cannot_corrupt_the_log():
    """Commit subjects are arbitrary text from someone else's repository."""
    assert _log_safe("one\rtwo\nthree") == "one two three"
    assert _log_safe("\x1b[31mred\x1b[0m") == "[31mred [0m"
    assert _log_safe("naïve — dash") == "na?ve ? dash"
    # An all-control subject collapses to nothing and is then dropped entirely.
    assert describe({"id": "c1", "title": "\r\n\t"}, None) == "c1"
