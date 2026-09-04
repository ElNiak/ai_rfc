import hashlib
from pathlib import Path

import pytest

from ai_rfc.draft.checkpoint import MANIFEST_FILE
from ai_rfc.experiment.optimize.fixtures import (
    INTERVIEW_TRANSCRIPT,
    InterviewFixture,
    build_interview_pristine,
)
from ai_rfc.experiment.workspace import HARNESS_MARKER, copy_workspace
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
