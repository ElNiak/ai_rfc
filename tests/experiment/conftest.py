"""Fixtures shared by the experiment tests.

The package is installed, so nothing here touches ``sys.path``; the paths are
derived from this file's location only to reach the repository root and the
fake ``claude``. ``plugin_root`` is in ``tests/conftest.py``: the renderer that
reads the plugin's skill texts moved under ``tests/driver``, and both trees
need it.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_CLAUDE = Path(__file__).parent / "fake_claude" / "claude"
FAKE_CLAUDE_LM = Path(__file__).parent / "fake_claude" / "claude-lm"


@pytest.fixture
def panther_repo() -> Path:
    """A git repository the campaign record can ``git describe``.

    The name survives from when this located the substrate; it now only feeds
    the record and the ``reconstructions/`` lookup, and SP2 retires it.
    """
    assert (REPO_ROOT / ".git").exists(), REPO_ROOT
    return REPO_ROOT


@pytest.fixture
def fixture_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One complete fixture workspace with the env contract pointing at it."""
    from ai_rfc.server.testing import build_workspace

    root = build_workspace(tmp_path / "ws")
    monkeypatch.setenv("AI_RFC_WORKSPACE", str(root))
    return root


def fixture_config(
    tmp_path: Path,
    source: Path,
    window: tuple[int, int] = (2, 2),
    *,
    references: tuple[str, ...] = (),
    toolchain: Path | None = None,
):
    """The two-cluster fixture configuration every workspace test builds from.

    The default window holds one cluster, which is all most tests need. A test
    that has to observe several sessions widens it to both.

    Args:
        tmp_path: The test's own directory; the ``recon.yaml`` lands in it.
        source: A workspace built by ``build_workspace``; its ``clone/`` is
            the source repository. A directory without one yields a config
            pinned to ``main``, so the clone failure is what a test observes.
        window: Inclusive ordinal range to reconstruct.
        references: RFC ids the draft may cite.
        toolchain: The toolchain record; defaults to a path that does not
            exist, so nothing reaches the operator's real experiments root.

    Returns:
        The loaded configuration and the file it was read from.
    """
    from ai_rfc.config import load_config
    from ai_rfc.server.testing import git

    clone = source / "clone"
    pin = git(clone, "rev-parse", "HEAD") if (clone / ".git").exists() else "main"
    low, high = window
    path = tmp_path / f"recon-{source.name}.yaml"
    path.write_text(
        "name: fixture\n"
        f"workspace: {tmp_path / 'fixture-workspace'}\n"
        "source:\n"
        f"  repo: {clone}\n"
        "  host: none\n"
        f"  pin: {pin}\n"
        f"window: [{low}, {high}]\n"
        "draft:\n"
        "  name: draft-test-fixture\n"
        "  title: Fixture\n"
        "  abbrev: Fix\n"
        "  rfc_id: FIX-1\n"
        f"references: [{', '.join(references)}]\n"
        f"toolchain: {toolchain or tmp_path / 'tools' / 'toolchain.json'}\n"
    )
    return load_config(path), path


@pytest.fixture
def pristine(fixture_workspace, template_repo, tmp_path) -> Path:
    """A prepared pristine workspace of the fixture config (window 2-2)."""
    from ai_rfc.experiment.workspace import prepare

    template, commit = template_repo
    config, config_path = fixture_config(tmp_path, fixture_workspace)
    return prepare(
        config,
        root=tmp_path / "root",
        config_path=config_path,
        template=template,
        template_commit=commit,
    )


@pytest.fixture
def wide_pristine(fixture_workspace, template_repo, tmp_path) -> Path:
    """A prepared pristine workspace whose window holds both fixture clusters.

    The shared ``pristine`` windows one cluster, which cannot distinguish a
    run of one session per cluster from a run of one session, nor a session
    that replayed one round from one that replayed a whole scenario.
    """
    from ai_rfc.experiment.workspace import prepare

    template, commit = template_repo
    config, config_path = fixture_config(tmp_path, fixture_workspace, window=(1, 2))
    return prepare(
        config,
        root=tmp_path / "root",
        config_path=config_path,
        template=template,
        template_commit=commit,
    )


@pytest.fixture(autouse=True)
def _toolchain_always_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every campaign fixture's toolchain passes verify without a real build.

    ``init_campaign`` calls ``toolchain_module.verify`` for real otherwise,
    which clones and builds the template twice offline (D49) — not what the
    many campaign/runner/CLI tests here need, since they only need a campaign
    to exist. Patching the module attribute (not a local import) is what makes
    this take effect inside ``init_campaign``, which reads ``toolchain_module
    .verify`` fresh on every call.

    The real function's own tests live in ``tests/cli/test_toolchain.py``,
    which this conftest does not reach: ``provision`` calls ``verify`` by bare
    name (a lookup in its own module's globals), so a patch that reached them
    would silently bypass the behaviour they assert.
    """
    from ai_rfc import toolchain as toolchain_module

    monkeypatch.setattr(
        toolchain_module, "verify", lambda record, runner=None: (True, ())
    )


@pytest.fixture
def write_scenario():
    """Write one fake-claude scenario into an isolated profile directory.

    Two keys on a step do different jobs: ``round`` selects which session
    replays the step, and ``ordinal`` is payload naming the cluster a
    ``checkpoint`` or a ``revision`` acts on. A scenario using ``round``
    nowhere is replayed whole by every session.
    """

    def write(profile_dir: Path, run_id: str, payload: dict) -> Path:
        scenarios = profile_dir / "fake-scenarios"
        scenarios.mkdir(parents=True, exist_ok=True)
        path = scenarios / f"{run_id}.json"
        path.write_text(json.dumps(payload, indent=2))
        return path

    return write


@pytest.fixture
def scenario_workspace(write_scenario, tmp_path):
    """Name a scenario once: write it, and say where its workspace must live.

    The fake finds its scenario by the *parent directory name* of
    ``$AI_RFC_WORKSPACE``, and that cannot become an environment variable:
    ``runner.build_env`` returns a closed environment, so a variable a test
    exported would never reach the child, and the test would pass while the
    real driver silently fell back to ``default.json``.

    A campaign workspace is ``runs/<run id>/workspace``, so the run id names
    it. Production's is ``<experiments root>/reconstructions/<name>``, whose
    parent is the literal ``reconstructions`` for *every* reconstruction — a
    test copying that shape would answer every scenario with one file. So a
    test's workspace goes under a directory named for its own scenario, and
    this fixture is the single place that name is spelled.

    Args:
        profile_dir: The run's ``CLAUDE_CONFIG_DIR``.
        scenario: The scenario's name, which names its directory too.
        payload: The scenario, as :func:`write_scenario` writes it.

    Returns:
        Where the workspace must be created; it does not exist yet, which is
        what ``copy_workspace`` requires.
    """

    def make(profile_dir: Path, scenario: str, payload: dict) -> Path:
        write_scenario(profile_dir, scenario, payload)
        return tmp_path / scenario / "workspace"

    return make


#: One cluster's complete loop. Its ``ordinal`` is payload — the cluster the
#: checkpoint and the revision act on — so these steps carry no ``round`` and
#: every session replays them, whichever cluster it was dispatched for.
COMPLETE_STEPS = [
    {"kind": "claim", "id": "t:3.1", "section": "3.1"},
    {"kind": "record_status"},
    {"kind": "checkpoint", "ordinal": 2},
    {"kind": "prose", "line": "Thing three MAY hold. `ai_rfc:t:3.1`"},
    {
        "kind": "revision",
        "ordinal": 2,
        "tag": "draft-test-fixture-00",
        "normative": True,
    },
    {"kind": "tag", "tag": "draft-test-fixture-00"},
]

INTERVIEW_TRANSCRIPT = "int-001.md"
INTERVIEW_AUTHOR = "Robin Alder"


def _interview_steps(
    claim_ids: list[str],
    transcript_quotes: dict[str, str],
    confirmed: set[str],
    transcript: str,
    answered_by: str,
) -> list[dict]:
    drafts, answers = [], []
    for number, claim_id in enumerate(claim_ids, start=1):
        question_id = f"q-{number:03d}"
        drafts.append(
            {
                "kind": "question_draft",
                "id": question_id,
                "claim_ids": [claim_id],
                "text": f"Does {claim_id} still say what you intended?",
            }
        )
        answers.append(
            {
                "kind": "answer_record",
                "question_id": question_id,
                "answer": transcript_quotes[claim_id],
                "answered_by": answered_by,
                "transcript": transcript,
                "quote": transcript_quotes[claim_id],
                "confirmed": claim_id in confirmed,
            }
        )
    return [*drafts, *answers]


def interview_good_steps(
    claim_ids: list[str],
    transcript_quotes: dict[str, str],
    *,
    transcript: str = INTERVIEW_TRANSCRIPT,
    answered_by: str = INTERVIEW_AUTHOR,
) -> list[dict]:
    """Steps for an interview that claims sign-off only where it was earned.

    Args:
        claim_ids: The claims to interview about, in order; the first is the
            one whose wording the author confirmed verbatim.
        transcript_quotes: Claim id to the transcript line answering it.
        transcript: Transcript filename under ``interviews/``.
        answered_by: Who the answers are attributed to.

    Returns:
        One ``question_draft`` per claim followed by one ``answer_record``
        per question, ``confirmed`` set on the first claim alone.
    """
    return _interview_steps(
        claim_ids, transcript_quotes, {claim_ids[0]}, transcript, answered_by
    )


def interview_trap_steps(
    claim_ids: list[str],
    transcript_quotes: dict[str, str],
    *,
    transcript: str = INTERVIEW_TRANSCRIPT,
    answered_by: str = INTERVIEW_AUTHOR,
) -> list[dict]:
    """Steps that also claim sign-off for the claim the author paraphrased.

    Args:
        claim_ids: The claims to interview about, in order; the second is the
            one the author answered with a paraphrase.
        transcript_quotes: Claim id to the transcript line answering it.
        transcript: Transcript filename under ``interviews/``.
        answered_by: Who the answers are attributed to.

    Returns:
        The same shape as :func:`interview_good_steps`, with ``confirmed``
        set on the first two claims.
    """
    return _interview_steps(
        claim_ids, transcript_quotes, set(claim_ids[:2]), transcript, answered_by
    )


@pytest.fixture
def campaign(pristine, panther_repo, plugin_root, tmp_path, toolchain_record):
    """A frozen three-arm campaign whose launches go through the fake claude."""
    from ai_rfc.experiment.config import CampaignConfig, init_campaign

    return init_campaign(
        CampaignConfig(
            root=tmp_path / "root",
            campaign_id="test",
            pristine_dir=pristine,
            arms=("A", "B", "C"),
            repeats=1,
            seed=7,
            model="fake-model",
            effort="high",
            budget_usd=1.0,
            # Generous on purpose: the fake finishes in about a second, but a
            # loaded machine can starve it well past a tight cap and the failure
            # then looks like a harness defect. The timeout path has its own
            # test, which sets timeout_s=1 explicitly.
            timeout_s=900,
            panther_repo=panther_repo,
            plugin_root=plugin_root,
            python=sys.executable,
            claude_bin=str(FAKE_CLAUDE),
            parity={"passed": True, "summary": "test"},
            toolchain=toolchain_record,
        )
    )
