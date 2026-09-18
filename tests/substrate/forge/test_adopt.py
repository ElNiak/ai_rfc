import json
from pathlib import Path

import pytest

from ai_rfc.forge.adopt import adopt_snapshot, read_records
from ai_rfc.forge.store import ForgeError, read_snapshot, write_snapshot

pytestmark = pytest.mark.unit


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_records_round_trip_through_the_snapshot_writer(tmp_path: Path):
    """Adopt must reuse the writer, not reimplement the on-disk contract.

    Four downstream readers re-parse these bytes directly, so a second
    producer that hand-wrote them would drift from the one that is tested.
    """
    pull = {"number": 2, "merged_at": "x", "merge_commit_sha": "b" * 40}
    src = _write(
        tmp_path / "records.json",
        {"pulls": [pull], "reviews": [], "comments": []},
    )

    pulls, reviews, comments = read_records(src)
    assert pulls == [pull]
    assert reviews == [] and comments == []

    snapshot = write_snapshot(
        tmp_path / "out",
        host="gitlab.example",
        owner="o",
        repo="r",
        kind="gitlab",
        clone_head="a" * 40,
        fetched_at="2026-09-01T00-00-00Z",
        authenticated=False,
        pulls=pulls,
        reviews=reviews,
        comments=comments,
        acquisition="adopt",
        fidelity_ceiling="pulls",
    )
    meta = json.loads((snapshot / "meta.json").read_text())
    assert meta["acquisition"] == "adopt"
    assert meta["fidelity_ceiling"] == "pulls"
    assert json.loads((snapshot / "pulls.jsonl").read_text().strip()) == pull


def test_a_records_file_that_is_not_an_object_is_refused(tmp_path: Path):
    src = _write(tmp_path / "records.json", [1, 2, 3])
    with pytest.raises(ForgeError, match="object"):
        read_records(src)


def test_a_file_that_is_not_utf_8_is_refused(tmp_path: Path):
    """A dump from a latin-1 toolchain must be a diagnostic, not a traceback.

    UnicodeDecodeError is a ValueError, so it escapes both an OSError guard
    here and the CLI's (ForgeError, OSError) handler unless it is named.
    """
    src = tmp_path / "records.json"
    src.write_bytes(b'{"pulls": []}\xff\xfe')
    with pytest.raises(ForgeError):
        read_records(src)


def test_a_section_that_is_not_a_list_of_objects_is_refused(tmp_path: Path):
    src = _write(tmp_path / "records.json", {"pulls": ["not-an-object"]})
    with pytest.raises(ForgeError, match="pulls"):
        read_records(src)


def test_absent_sections_read_as_empty(tmp_path: Path):
    """A forge that has no reviews omits the key rather than writing null."""
    src = _write(tmp_path / "records.json", {"pulls": []})
    assert read_records(src) == ([], [], [])


def test_an_unknown_comment_kind_is_refused_by_the_writer(tmp_path: Path):
    """Validation is inherited from write_snapshot, not duplicated here.

    read_records deliberately passes the row through; the refusal must come
    from the one writer that owns the contract, so that an adopted snapshot
    cannot carry anything a fetched one could not.
    """
    src = _write(
        tmp_path / "records.json",
        {"comments": [{"pr_number": 1, "id": 1, "kind": "gossip"}]},
    )
    _, _, comments = read_records(src)
    assert comments[0]["kind"] == "gossip"

    with pytest.raises(ForgeError, match="gossip"):
        write_snapshot(
            tmp_path / "out",
            host="gitlab.example",
            owner="o",
            repo="r",
            kind="gitlab",
            clone_head="a" * 40,
            fetched_at="2026-09-01T00-00-02Z",
            authenticated=False,
            pulls=[],
            reviews=[],
            comments=comments,
            acquisition="adopt",
            fidelity_ceiling="pulls",
        )


def _snapshot(out_root: Path, *, fetched_at: str = "2026-08-25T15-16-59Z") -> Path:
    """A snapshot carrying discussion, written the only way snapshots are."""
    return write_snapshot(
        out_root,
        host="forge.example",
        owner="aiortc",
        repo="aioquic",
        kind="gitlab",
        clone_head="6d36838d008c2202c337142fa07e8bf80e96bac8",
        fetched_at=fetched_at,
        authenticated=True,
        pulls=[{"number": 7, "merged_at": "x", "merge_commit_sha": "c" * 40}],
        reviews=[{"pr_number": 7, "id": 1, "state": "APPROVED"}],
        comments=[
            {
                "pr_number": 7,
                "id": 2,
                "kind": "issue_comment",
                "created_at": "2026-08-01T00:00:00Z",
                "body": "b",
            }
        ],
    )


def test_adopting_a_snapshot_carries_its_rows_and_its_provenance(tmp_path: Path):
    """A snapshot another operator fetched is re-filed without being refetched.

    The rows must survive byte-for-byte, because the timeline clusters from
    ``pulls.jsonl`` alone and a reconstruction's cluster ids are only stable
    while those rows are. What must *not* survive is the claim that this
    process fetched them: ``acquisition`` says ``adopt``.
    """
    source = _snapshot(tmp_path / "source")

    adopted = adopt_snapshot(source, tmp_path / "workspace-forge")

    before, after = read_snapshot(source), read_snapshot(adopted)
    assert after["pulls"] == before["pulls"]
    assert after["reviews"] == before["reviews"]
    assert after["comments"] == before["comments"]

    meta = after["meta"]
    assert meta["acquisition"] == "adopt"
    assert before["meta"]["acquisition"] == "api"
    for carried in ("host", "owner", "repo", "kind", "clone_head", "fetched_at"):
        assert meta[carried] == before["meta"][carried], carried
    # `complete` is what tells a narrower reconstruction from a broken one, so
    # adopting a whole snapshot must not quietly downgrade it.
    assert meta["complete"] is True
    assert meta["fidelity_ceiling"] == before["meta"]["fidelity_ceiling"]


def test_adopting_refuses_to_overwrite_a_snapshot_already_there(tmp_path: Path):
    """The write-once rule is the writer's, and adopting does not escape it."""
    source = _snapshot(tmp_path / "source")
    out = tmp_path / "workspace-forge"
    adopt_snapshot(source, out)

    with pytest.raises(ForgeError, match="written once"):
        adopt_snapshot(source, out)
