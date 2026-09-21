"""The campaign audit's half of the coverage ruling: candidates are siblings.

``mark-dry-49-51`` is the shape these fixtures reproduce. Its
``A1.interrupted-detached`` run holds an on-disk ``c0049`` checkpoint whose
receipt appears **only** in the sibling ``A1.interrupted-shell-exit``'s
transcript, and every receipt in that campaign names ``runs/A1/workspace/…``
— the directory name that existed before ``record.move_aside`` renamed the
run. A run-local reader, or one joining on the absolute path, reports that
checkpoint as unattested, which reads as tampering when it is nothing of the
sort.
"""

import json

from ai_rfc import ledger
from ai_rfc.experiment.audit import audit_run, run_coverage

DETACHED = "A1.interrupted-detached"
SHELL_EXIT = "A1.interrupted-shell-exit"
C49 = "c0049-pr-ba8ca432c304"
C50 = "c0050-epoch-e058f3e6fcef"
DIGEST = "6d953ee6" + "0" * 56


def _status(run_dir, run_id):
    (run_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "arm": "A",
                "repeat": 1,
                "started_at": "2026-09-08T09:00:00+00:00",
                "finished_at": "2026-09-08T09:30:00+00:00",
                "exit_code": 0,
                "timed_out": False,
                "budget_hit": False,
                "claude_version": "fake",
            },
            sort_keys=True,
        )
        + "\n"
    )


def _checkpoint(run_dir, cluster, *, pre_seeded=False):
    directory = run_dir / "workspace" / "checkpoints" / cluster
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ledger.CHECKPOINT_FILE).write_text(
        json.dumps({"cluster_id": cluster}, sort_keys=True) + "\n"
    )
    if pre_seeded:
        (directory / ledger.PRESEED_MARKER).write_text("{}\n")


def _receipt_events(receipt_dir, cluster, use_id="use-1"):
    envelope = json.dumps(
        {
            "exit_code": 0,
            "stderr": [f"note: checkpoint written to {receipt_dir}/{cluster}"],
            "manifest_sha256": DIGEST,
        }
    )
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": use_id,
                        "name": "mcp__ai_rfc__ai_rfc_checkpoint",
                        "input": {"cluster_id": cluster},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": use_id,
                        "content": [{"type": "text", "text": envelope}],
                    }
                ]
            },
        },
    ]


def _events(run_dir, events):
    (run_dir / "events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
    )


def _two_siblings(campaign):
    """The ``mark-dry`` shape: the artifact here, its receipt next door."""
    detached = campaign.runs_dir / DETACHED
    shell_exit = campaign.runs_dir / SHELL_EXIT
    for run_dir, run_id in ((detached, DETACHED), (shell_exit, SHELL_EXIT)):
        run_dir.mkdir(parents=True, exist_ok=True)
        _status(run_dir, run_id)
    _checkpoint(detached, C49)
    _events(detached, [])
    # The receipt names the run directory as it was before it was moved aside.
    _events(
        shell_exit,
        _receipt_events(campaign.runs_dir / "A1" / "workspace" / "checkpoints", C49),
    )
    return detached, shell_exit


def test_a_sibling_receipt_covers_this_runs_checkpoint_and_names_the_sibling(campaign):
    _two_siblings(campaign)
    recorded = run_coverage(campaign, DETACHED)
    assert recorded["checkpoints"] == {C49: {"route": "receipt", "source": SHELL_EXIT}}
    assert recorded["unverified"] == []
    assert recorded["unreadable"] == []


def test_the_audit_record_carries_the_coverage(campaign):
    _two_siblings(campaign)
    audit = audit_run(campaign, DETACHED)
    assert audit["coverage"]["checkpoints"][C49]["route"] == "receipt"
    assert audit["coverage"]["checkpoints"][C49]["source"] == SHELL_EXIT
    written = json.loads((campaign.audit_dir / f"{DETACHED}.json").read_text())
    assert written["coverage"] == audit["coverage"]


def test_a_checkpoint_no_sibling_vouches_for_is_unverified(campaign):
    detached, _shell_exit = _two_siblings(campaign)
    _checkpoint(detached, C50)
    recorded = run_coverage(campaign, DETACHED)
    assert recorded["checkpoints"][C50] == {"route": None, "source": None}
    assert recorded["unverified"] == [C50]


def test_a_pre_seed_marker_still_covers_without_any_transcript(campaign):
    detached, _shell_exit = _two_siblings(campaign)
    _checkpoint(detached, C50, pre_seeded=True)
    recorded = run_coverage(campaign, DETACHED)
    assert recorded["checkpoints"][C50] == {"route": "pre_seed", "source": None}


def test_this_runs_own_session_row_covers_by_the_session_route(campaign):
    detached, _shell_exit = _two_siblings(campaign)
    _checkpoint(detached, C50)
    (detached / "sessions.jsonl").write_text(
        json.dumps({"cluster_id": C50, "kind": "cluster"}, sort_keys=True) + "\n"
    )
    recorded = run_coverage(campaign, DETACHED)
    assert recorded["checkpoints"][C50] == {"route": "session", "source": DETACHED}


def test_a_damaged_sibling_transcript_is_named_and_raises_nothing(campaign):
    detached, shell_exit = _two_siblings(campaign)
    (shell_exit / "events.jsonl").write_text('{"type": "assistant"}\n{"type": "us')
    recorded = run_coverage(campaign, DETACHED)
    assert recorded["checkpoints"][C49] == {"route": None, "source": None}
    assert len(recorded["unreadable"]) == 1
    assert recorded["unreadable"][0].startswith("cannot adjudicate: ")
    assert SHELL_EXIT in recorded["unreadable"][0]
