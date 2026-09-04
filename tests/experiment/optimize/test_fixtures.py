import dataclasses
import hashlib
import shutil
from pathlib import Path

import pytest

from ai_rfc.draft.checkpoint import MANIFEST_FILE
from ai_rfc.experiment import ExperimentError
from ai_rfc.experiment.optimize.fixtures import (
    INTERVIEW_TRANSCRIPT,
    InterviewFixture,
    build_interview_pristine,
    load_interview_fixture,
    sidecar_path,
)
from ai_rfc.experiment.workspace import HARNESS_MARKER, copy_workspace, verify_digest
from ai_rfc.promotion import adjudicate
from ai_rfc.schema import load


@pytest.fixture
def interview(tmp_path, panther_repo, template_repo) -> InterviewFixture:
    template, commit = template_repo
    return build_interview_pristine(
        tmp_path / "root",
        panther_repo=panther_repo,
        template=template,
        template_commit=commit,
    )


def _checkpointed(fixture: InterviewFixture) -> Path:
    """The manifest frozen by the session's own checkpoint, not a pre-seed."""
    frozen = [
        directory
        for directory in (fixture.pristine_dir / "checkpoints").iterdir()
        if not (directory / HARNESS_MARKER).exists()
    ]
    assert len(frozen) == 1, frozen
    return frozen[0] / MANIFEST_FILE


def test_the_three_claims_adjudicate_to_gap_inferred_inferred(interview):
    manifest = load(_checkpointed(interview))
    supported = {claim.id: adjudicate(claim).value for claim in manifest.claims}

    assert supported == {
        interview.exact_claim: "gap",
        interview.paraphrase_claim: "inferred",
        interview.correction_claim: "inferred",
    }


def test_the_transcript_is_planted_with_the_recorded_digest(interview):
    transcript = interview.pristine_dir / "interviews" / INTERVIEW_TRANSCRIPT

    assert transcript.exists()
    assert (
        hashlib.sha256(transcript.read_bytes()).hexdigest()
        == interview.transcript_sha256
    )


def test_only_the_exact_claim_is_quoted_verbatim_in_the_transcript(interview):
    transcript = (
        interview.pristine_dir / "interviews" / INTERVIEW_TRANSCRIPT
    ).read_text()
    texts = {claim.id: claim.text for claim in load(_checkpointed(interview)).claims}

    assert set(interview.quotes) == set(texts)
    for claim_id, quote in interview.quotes.items():
        assert quote in transcript, claim_id
    assert texts[interview.exact_claim] in interview.quotes[interview.exact_claim]
    assert texts[interview.paraphrase_claim] not in transcript
    assert texts[interview.correction_claim] not in transcript


def test_the_pristine_copies_and_verifies_like_any_other(interview, tmp_path):
    assert (interview.pristine_dir / "pristine.sha256").exists()
    assert (interview.pristine_dir / "pristine.json").exists()

    copy_workspace(interview.pristine_dir, tmp_path / "run" / "workspace")


def test_the_sidecar_round_trips_the_fixture_the_build_returned(interview):
    assert load_interview_fixture(interview.pristine_dir) == interview


def test_a_moved_baseline_loads_its_sidecar_under_the_new_path(interview, tmp_path):
    """The baseline's own path is never stored, so a copy is not stale."""
    moved = tmp_path / "elsewhere" / interview.pristine_dir.name
    moved.parent.mkdir(parents=True)
    shutil.copytree(interview.pristine_dir, moved)
    shutil.copyfile(sidecar_path(interview.pristine_dir), sidecar_path(moved))

    assert load_interview_fixture(moved) == dataclasses.replace(
        interview, pristine_dir=moved
    )


def test_the_sidecar_sits_outside_the_baseline_every_run_copies(interview, tmp_path):
    """It names which of the three claims the author confirmed word for word.

    A run's workspace is copied from the baseline whole, so a sidecar stored
    inside it would hand the session under measurement the sign-off trap's
    answer — and would be an unexpected file to the digest guard besides.
    """
    sidecar = sidecar_path(interview.pristine_dir)
    assert sidecar.is_file()
    assert interview.pristine_dir not in sidecar.parents
    assert verify_digest(interview.pristine_dir) == []

    workspace = copy_workspace(interview.pristine_dir, tmp_path / "run" / "workspace")

    assert list(workspace.rglob(sidecar.name)) == []


def test_a_baseline_without_a_sidecar_says_which_file_is_missing(interview):
    sidecar_path(interview.pristine_dir).unlink()

    with pytest.raises(ExperimentError, match=r"interview\.json"):
        load_interview_fixture(interview.pristine_dir)
