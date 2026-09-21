"""``ai_rfc.driver.coverage``: what covered a checkpoint, and what could not.

Every transcript below is built from spellings **copied out of** the archived
evidence — the six pre-CLI-2 pilot runs under ``~/arfc-experiments`` and the
post-rename ``mark-full-1`` run under ``~/ai-rfc-experiments``. The archives
are read-only evidence: nothing in this file reads or writes them, and the
cluster ids, command lines and result envelopes are reproduced here so the
suite is self-contained.

Two spellings are deliberately *not* copied: arm B's current command prefix
and the pre-seed and checkpoint marker names, which are read from the one
declaration each has, so a drifted second copy cannot make this suite agree
with itself and disagree with the code.
"""

import json
from pathlib import Path, PurePosixPath

from ai_rfc import ledger
from ai_rfc.driver.arms import arm_profile
from ai_rfc.driver.coverage import (
    ALIASES,
    Route,
    checkpoint_calls,
    covers,
    read_transcript,
    receipts,
)
from ai_rfc.driver.enforcement import bash_prefixes

#: Arm B's prefix today, from the one declaration in ``driver.arms``.
(ARM_B_PREFIX,) = bash_prefixes(arm_profile("B"))

# Cluster ids and manifest digests as the pilot's A1/B1/C1 transcripts carry
# them. A2 holds the pair that made `(cluster_id, sha)` the key: c0003 and
# c0004 were checkpointed from an unchanged manifest and share one digest.
C2 = "c0002-pr-60258445de47"
C3 = "c0003-epoch-f731035b44b5"
SHA_C2 = "ad7f8b844a420fd4ab33da4f2800fb50973cc240a20e79a079ee1c025b229baa"
SHA_C3 = "e817d8a95b1e5666f107863514899aab309ca50ae0fcdb361a068230d8cfb1d2"

#: The multi-verb line B1 really ran, and which its guard denied.
ARCHIVED_HELP_SWEEP = (
    "arfc claim-upsert --help; echo ---; arfc checkpoint --help; echo ---; "
    "arfc revision-record --help"
)


def _pair(use_id: str, name: str, tool_input: dict, text: str) -> list[dict]:
    """One ``tool_use`` and the ``tool_result`` that answered it."""
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": use_id,
                        "name": name,
                        "input": tool_input,
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
                        "content": [{"type": "text", "text": text}],
                    }
                ]
            },
        },
    ]


def _envelope(checkpoints: Path | PurePosixPath, cluster: str, sha: str) -> str:
    """The JSON envelope arms A and B answer a write with."""
    return json.dumps(
        {
            "exit_code": 0,
            "stderr": [f"note: checkpoint written to {checkpoints}/{cluster}"],
            "manifest_sha256": sha,
        }
    )


def _bare(checkpoints: Path | PurePosixPath, cluster: str) -> str:
    """The stdout line arm C answers a write with; it carries no digest."""
    return f"note: checkpoint written to {checkpoints}/{cluster}"


def _mcp(checkpoints, cluster, sha, *, tool, use_id="u-mcp") -> list[dict]:
    return _pair(
        use_id, tool, {"cluster_id": cluster}, _envelope(checkpoints, cluster, sha)
    )


def _cli(checkpoints, cluster, sha, *, prefix, use_id="u-cli") -> list[dict]:
    return _pair(
        use_id,
        "Bash",
        {
            "command": f"{prefix}checkpoint {cluster}",
            "description": "Checkpoint cluster",
        },
        _envelope(checkpoints, cluster, sha),
    )


def _module(checkpoints, cluster, *, dotted, use_id="u-mod") -> list[dict]:
    command = (
        f"python -m {dotted} checkpoint {checkpoints}/../manifest.yaml "
        f"--timeline {checkpoints}/../timeline --cluster {cluster} "
        f"--out {checkpoints}"
    )
    return _pair(use_id, "Bash", {"command": command}, _bare(checkpoints, cluster))


def _era_one(checkpoints, cluster, sha) -> list[list[dict]]:
    """Arms A, B and C as the pilot's transcripts spell them."""
    return [
        _mcp(checkpoints, cluster, sha, tool="mcp__arfc__arfc_checkpoint"),
        _cli(checkpoints, cluster, sha, prefix="arfc "),
        _module(
            checkpoints,
            cluster,
            dotted="panther.plugins.services.testers.a_rfc.draft",
        ),
    ]


def _era_two(checkpoints, cluster, sha) -> list[list[dict]]:
    """The same three arms after the rename."""
    return [
        _mcp(checkpoints, cluster, sha, tool="mcp__ai_rfc__ai_rfc_checkpoint"),
        _cli(checkpoints, cluster, sha, prefix=ARM_B_PREFIX),
        _module(checkpoints, cluster, dotted="ai_rfc.draft"),
    ]


def _checkpoints(tmp_path: Path, *clusters: str, pre_seeded: tuple = ()) -> Path:
    """A checkpoints directory holding one written checkpoint per cluster."""
    root = tmp_path / "workspace" / "checkpoints"
    for cluster in clusters:
        directory = root / cluster
        directory.mkdir(parents=True)
        (directory / ledger.CHECKPOINT_FILE).write_text("{}\n")
        if cluster in pre_seeded:
            (directory / ledger.PRESEED_MARKER).write_text("{}\n")
    return root


def test_every_write_shape_of_the_first_era_covers_by_receipt(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    for events in _era_one(checkpoints, C2, SHA_C2):
        assert covers(checkpoints, session_rows=[], transcripts=[events]) == {
            C2: Route.receipt
        }


def test_every_write_shape_of_the_second_era_covers_by_receipt(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    for events in _era_two(checkpoints, C2, SHA_C2):
        assert covers(checkpoints, session_rows=[], transcripts=[events]) == {
            C2: Route.receipt
        }


def test_a_help_invocation_is_not_a_write_however_its_result_reads(tmp_path):
    """B1 really ran ``arfc checkpoint --help`` beside its ten real writes.

    The result text is the receipt on purpose: the gate is the *call*, and a
    matcher that read ``parts[2]`` would have taken ``--help`` for an id.
    """
    checkpoints = _checkpoints(tmp_path, C2)
    events = _pair(
        "u-help",
        "Bash",
        {"command": "arfc checkpoint --help"},
        _envelope(checkpoints, C2, SHA_C2),
    )
    assert checkpoint_calls(events) == []
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_a_compound_command_is_never_a_write(tmp_path):
    """The shell, not the writer, produced the note on the second command."""
    checkpoints = _checkpoints(tmp_path, C2)
    command = (
        f"{ARM_B_PREFIX}checkpoint {C2} ; "
        f"echo 'note: checkpoint written to {checkpoints}/{C2}'"
    )
    events = _pair("u-mix", "Bash", {"command": command}, _bare(checkpoints, C2))
    assert checkpoint_calls(events) == []
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_the_archived_multi_verb_help_line_is_not_a_write(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    events = _pair(
        "u-sweep",
        "Bash",
        {"command": ARCHIVED_HELP_SWEEP},
        _envelope(checkpoints, C2, SHA_C2),
    )
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_a_command_substitution_is_never_a_write(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    command = f"{ARM_B_PREFIX}checkpoint $(cat /tmp/id)"
    events = _pair("u-sub", "Bash", {"command": command}, _bare(checkpoints, C2))
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_a_session_row_naming_the_cluster_covers_it(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    rows = [{"cluster_id": C2}, {"cluster_id": None}]
    assert covers(checkpoints, session_rows=rows, transcripts=[]) == {C2: Route.session}


def test_a_pre_seed_marker_covers_a_checkpoint(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2, pre_seeded=(C2,))
    assert covers(checkpoints, session_rows=[], transcripts=[]) == {C2: Route.pre_seed}


def test_a_checkpoint_with_no_record_at_all_is_uncovered(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    assert covers(checkpoints, session_rows=[], transcripts=[]) == {C2: None}


def test_a_truncated_transcript_names_the_damage_and_raises_nothing(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    lines = [json.dumps(event) for event in _era_one(checkpoints, C2, SHA_C2)[0]]
    transcript = tmp_path / "events.jsonl"
    transcript.write_text(lines[0] + "\n" + lines[1][:40])
    events, damage = read_transcript(transcript)
    assert events == []
    assert damage is not None
    assert damage.startswith("cannot adjudicate: ")
    assert str(transcript) in damage and "line 2" in damage
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_an_absent_transcript_names_its_absence_and_raises_nothing(tmp_path):
    events, damage = read_transcript(tmp_path / "never" / "events.jsonl")
    assert events == []
    assert damage is not None and damage.startswith("cannot adjudicate: ")


def test_an_intact_transcript_reports_no_damage(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    transcript = tmp_path / "events.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(e) for e in _era_one(checkpoints, C2, SHA_C2)[0]) + "\n"
    )
    events, damage = read_transcript(transcript)
    assert damage is None
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {
        C2: Route.receipt
    }


def test_two_transcripts_carrying_the_same_receipt_count_once(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    first = _mcp(
        checkpoints, C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint", use_id="one"
    )
    second = _mcp(
        checkpoints, C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint", use_id="two"
    )
    assert len({**receipts(first), **receipts(second)}) == 1
    assert covers(checkpoints, session_rows=[], transcripts=[first, second]) == {
        C2: Route.receipt
    }


def test_the_key_is_the_cluster_and_the_digest_never_the_digest_alone(tmp_path):
    """A2 checkpointed two clusters from one unchanged manifest."""
    checkpoints = _checkpoints(tmp_path, C2, C3)
    shared = _mcp(
        checkpoints, C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint", use_id="one"
    ) + _mcp(checkpoints, C3, SHA_C2, tool="mcp__arfc__arfc_checkpoint", use_id="two")
    assert len(receipts(shared)) == 2
    assert covers(checkpoints, session_rows=[], transcripts=[shared]) == {
        C2: Route.receipt,
        C3: Route.receipt,
    }


def test_a_receipt_naming_another_cluster_than_its_call_covers_nothing(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2, C3)
    events = _pair(
        "u-cross",
        "mcp__arfc__arfc_checkpoint",
        {"cluster_id": C2},
        _envelope(checkpoints, C3, SHA_C3),
    )
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {
        C2: None,
        C3: None,
    }


def test_a_receipt_written_before_the_run_was_renamed_still_covers(tmp_path):
    """``move_aside`` renames a run *after* its receipts were written.

    Measured on ``mark-dry-49-51``: every receipt in both
    ``A1.interrupted-*`` transcripts names ``runs/A1/workspace/checkpoints``,
    a directory that no longer exists. So the join is the ``checkpoints/<id>``
    tail, and which transcripts are candidates is the caller's decision.
    """
    checkpoints = _checkpoints(tmp_path, C2)
    before_rename = PurePosixPath("/roots/campaigns/c/runs/A1/workspace/checkpoints")
    events = _mcp(before_rename, C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint")
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {
        C2: Route.receipt
    }


def test_a_receipt_outside_a_checkpoints_directory_covers_nothing(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    events = _mcp(
        PurePosixPath("/elsewhere"), C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint"
    )
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_an_errored_write_call_covers_nothing(tmp_path):
    """The writer refuses an existing directory; a refusal is not production."""
    checkpoints = _checkpoints(tmp_path, C2)
    events = _mcp(checkpoints, C2, SHA_C2, tool="mcp__arfc__arfc_checkpoint")
    events[1]["message"]["content"][0]["is_error"] = True
    assert covers(checkpoints, session_rows=[], transcripts=[events]) == {C2: None}


def test_a_directory_without_a_checkpoint_record_is_not_a_checkpoint(tmp_path):
    """What ``move_aside`` left behind is evidence, not an uncovered artifact."""
    checkpoints = tmp_path / "checkpoints"
    (checkpoints / f"{C2}.interrupted-20260901T000000Z").mkdir(parents=True)
    assert covers(checkpoints, session_rows=[], transcripts=[]) == {}


def test_an_absent_checkpoints_directory_yields_no_checkpoints(tmp_path):
    assert covers(tmp_path / "none", session_rows=[], transcripts=[]) == {}


def test_a_checkpoint_call_carries_the_id_its_result_joins_on(tmp_path):
    checkpoints = _checkpoints(tmp_path, C2)
    events = _cli(checkpoints, C2, SHA_C2, prefix=ARM_B_PREFIX, use_id="joinable")
    (call,) = checkpoint_calls(events)
    assert call["id"] == "joinable"
    assert call["cluster_id"] == C2
    assert call["arm"] == "B"


def test_the_alias_table_holds_two_dated_eras_and_names_what_closed_the_first():
    assert len(ALIASES) == 2
    first, second = ALIASES
    assert first.closed == "2026-09-01" and second.closed is None
    assert "a_rfc" in (first.closed_by or "")
    assert first.first_seen and second.first_seen


def test_the_current_eras_prefixes_are_derived_from_the_arm_not_spelled_again():
    assert ALIASES[-1].cli_prefixes == bash_prefixes(arm_profile("B"))
    assert ARM_B_PREFIX not in ALIASES[0].cli_prefixes
