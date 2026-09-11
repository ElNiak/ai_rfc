"""The CLI-2 gate: the acceptance criterion the design spec states for the row.

    A two-cluster MARK sweep completes and resumes after a kill; a budget stop
    reproduces the resume line.

All three go through the production door — ``ai-rfc init`` then ``ai-rfc
run`` — over a workspace whose ``sessions.claude`` is
``tests/experiment/fake_claude/claude``. **Nothing spends.** The fake really
mutates the workspace through the server core, so what these assert is the
substrate's own record of the work and not a simulation of it: the build gate
at the end of criterion 1 runs ``check --strict``, ``lint --strict`` and
``build``, none of them monkeypatched. The first two are the substrate's real
checks; ``build`` runs against ``toolchain_record``'s no-op executables, so it
proves the gate's wiring rather than a compiled draft.

Three facts about the fixture are load-bearing.

*The source repository has to hold a merge.* Two linear commits cluster into
one epoch, and a one-cluster sweep cannot distinguish a driver that spawns a
session per cluster from one that spawns a single session and stops.

*The workspace has to live under a directory named for its scenario.* The fake
selects its scenario by the parent directory name of ``$AI_RFC_WORKSPACE``,
and that cannot be an environment variable because
:func:`~ai_rfc.driver.session.session_env` returns a closed environment — a
variable a test exported would never reach the child, and the test would pass
while the driver silently replayed ``default.json``.

*The configuration has to name a toolchain record that exists.* ``toolchain``
falls back to ``<experiments root>/tools/toolchain.json`` and is therefore
never None, so ``AI_RFC_TOOLCHAIN`` is always set in a session's environment
and ``resolve_context`` refuses a path that is not a file. A session launched
without one dies before reaching the model, which the sweep reports as
``session_failed`` — a launch failure that reads exactly like a driver defect.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_rfc import cli, ledger
from ai_rfc.driver import record, sweep
from ai_rfc.driver.stop import StopReason, resume_line
from ai_rfc.driver.stream import salvage_stream, session_ids
from ai_rfc.server.testing import git

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The stand-in for ``claude -p``. Derived from this file rather than imported
#: from ``tests/experiment``: the gate depends on the binary, not on that
#: package's fixtures.
FAKE_CLAUDE = REPO_ROOT / "tests" / "experiment" / "fake_claude" / "claude"
#: The installed console script, taken from the interpreter running the tests
#: so a subprocess cannot pick up a different ``ai-rfc`` off ``PATH``.
AI_RFC = Path(sys.executable).parent / "ai-rfc"

#: What one fake session's result event reports, and the figure every budget
#: here is set against.
SESSION_COST = 0.5
#: Long enough that a poll loop watching the session counter lands inside it,
#: short enough that three sleeping sessions do not dominate the suite. The
#: fake sleeps *before* it touches the workspace, so a kill inside this window
#: tears no round.
KILL_WINDOW_S = 1.5
#: An abstract that is not the skeleton's. ``draft.lint`` matches the
#: skeleton's own sentence, so a draft still carrying it fails ``lint
#: --strict`` — which the build gate of a finished sweep runs.
ABSTRACT = "This document specifies the fixture protocol as it was built."


@pytest.fixture
def merged_source(tmp_path: Path) -> Path:
    """A repository whose history clusters into exactly two clusters.

    The shape is ``server.testing.build_workspace``'s clone: a root commit, a
    feature branch, a direct push and a no-fast-forward merge, which the
    timeline reads as one epoch and one PR. Commit dates are pinned so the
    cluster ids are the same on every run.
    """
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("one\n")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "root", date="2026-01-01T00:00:01+00:00")
    git(repo, "checkout", "-q", "-b", "feat")
    (repo / "b.txt").write_text("two\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "feat work", date="2026-01-01T00:00:02+00:00")
    git(repo, "checkout", "-q", "main")
    (repo / "c.txt").write_text("three\n")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-q", "-m", "direct push", date="2026-01-01T00:00:03+00:00")
    git(
        repo,
        "merge",
        "--no-ff",
        "feat",
        "-m",
        "Merge branch 'feat'",
        date="2026-01-01T00:00:04+00:00",
    )
    return repo


def cluster_steps(ordinal: int, tag: str, claim_id: str) -> list[dict]:
    """Every step the ledger needs before it calls one cluster done.

    Each cluster mines a claim of its own, which is load-bearing twice over:
    the tag is gated on the strict manifest gate, and the strict citation gate
    then refuses a normative revision whose checkpoint manifest repeats the
    previous one — so a second round that recorded no new claim would roll its
    own tag back and leave its cluster unfinished.

    Args:
        ordinal: The cluster the checkpoint and the revision act on.
        tag: The revision tag recorded and annotated.
        claim_id: The claim mined and cited; its section is the half after the
            colon.

    Returns:
        The steps, in the order a session performs them.
    """
    section = claim_id.split(":", 1)[1]
    return [
        {"kind": "claim", "id": claim_id, "section": section},
        {"kind": "record_status"},
        {"kind": "checkpoint", "ordinal": ordinal},
        {
            "kind": "prose",
            "line": f"Cluster {ordinal}: a thing MAY hold. `ai_rfc:{claim_id}`",
        },
        {"kind": "revision", "ordinal": ordinal, "tag": tag, "normative": True},
        {"kind": "tag", "tag": tag},
    ]


def one_round(steps: list[dict], ordinal: int) -> list[dict]:
    """The same steps, marked as the round the session for ``ordinal`` replays."""
    return [{**step, "round": ordinal} for step in steps]


def consolidation_steps(base_ordinal: int, ordinal: int, tag: str) -> list[dict]:
    """The sweep-end editorial round, marked for the session that has no cluster.

    The driver schedules one as soon as no cluster is outstanding and exits 1
    if it records no revision, so a sweep cannot reach ``done`` without it.
    ``normative: False`` is what the round is for and what lets it pass the
    citation gate: its checkpoint freezes the same manifest as its base, and a
    *normative* revision over an unchanged manifest is refused.

    Args:
        base_ordinal: The cluster whose checkpoint the consolidation follows.
        ordinal: The consolidation's own number.
        tag: The revision tag it records and annotates.

    Returns:
        The steps, all carrying the sweep-end round marker.
    """
    return [
        {"kind": "prose", "abstract": ABSTRACT, "message": "consolidate"},
        {"kind": "checkpoint", "ordinal": base_ordinal, "consolidation": ordinal},
        {
            "kind": "revision",
            "ordinal": base_ordinal,
            "tag": tag,
            "normative": False,
            "kind_of": "consolidation",
            "checkpoint": f"consolidations/{ordinal:02d}",
        },
        {"kind": "tag", "tag": tag},
    ]


#: Two cluster rounds, one per session, and the sweep-end round that follows
#: them. This is the MARK sweep the criterion names.
SWEEP_STEPS: list[dict] = [
    *one_round(cluster_steps(1, "draft-test-fixture-00", "t:3.1"), 1),
    *one_round(cluster_steps(2, "draft-test-fixture-01", "t:4.1"), 2),
    *[
        {**step, "round": "end"}
        for step in consolidation_steps(2, 1, "draft-test-fixture-02")
    ],
]


@dataclass(frozen=True)
class Reconstruction:
    """A production workspace whose sessions launch the fake.

    Attributes:
        config: The ``recon.yaml`` the operator names, which is also what the
            resume line prints.
        workspace: The workspace ``init`` built.
        profile: ``CLAUDE_CONFIG_DIR``; the fake reads its scenario and writes
            its session counter here.
        scenario: The scenario's name, which is also the workspace's parent
            directory name.
        env: A complete environment for a subprocess ``ai-rfc``.
    """

    config: Path
    workspace: Path
    profile: Path
    scenario: str
    env: dict[str, str]

    def sessions(self, run_dir: Path) -> list[dict]:
        """The rows one run's ``sessions.jsonl`` holds, in order."""
        path = run_dir / "sessions.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def runs(self) -> list[Path]:
        """Every run directory, leftovers included, oldest first."""
        root = self.workspace / record.RUNS_DIR
        return sorted(root.iterdir()) if root.is_dir() else []

    def live_run(self) -> Path:
        """The one run directory that was not moved aside."""
        live = [p for p in self.runs() if record.INTERRUPTED not in p.name]
        assert len(live) == 1, [p.name for p in self.runs()]
        return live[0]


@pytest.fixture
def reconstruction(
    tmp_path: Path,
    merged_source: Path,
    template_repo: tuple[str, str],
    toolchain_record: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Build one initialised reconstruction driven by one fake scenario."""

    def build(
        scenario: str,
        steps: list[dict],
        *,
        budget_usd: float = 5.0,
        sleep: float | None = None,
    ) -> Reconstruction:
        workspace = tmp_path / scenario / "workspace"
        profile = tmp_path / f"profile-{scenario}"
        root = tmp_path / "root"
        env = {
            "AI_RFC_EXPERIMENTS_ROOT": str(root),
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", ""),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        config_path = tmp_path / f"recon-{scenario}.yaml"
        config_path.write_text(
            "name: fixture\n"
            f"workspace: {workspace}\n"
            "source:\n"
            f"  repo: {merged_source}\n"
            "  host: none\n"
            "  pin: main\n"
            "draft:\n"
            "  name: draft-test-fixture\n"
            f"toolchain: {toolchain_record}\n"
            "sessions:\n"
            f"  budget_usd: {budget_usd}\n"
            f"  claude: {FAKE_CLAUDE}\n"
            f"  profile: {profile}\n"
            "  timeout_s: 120\n"
        )
        payload: dict = {"arm": "A", "cost": SESSION_COST, "steps": steps}
        if sleep is not None:
            payload["sleep"] = sleep
        scenarios = profile / "fake-scenarios"
        scenarios.mkdir(parents=True)
        (scenarios / f"{scenario}.json").write_text(json.dumps(payload, indent=2))
        template, commit = template_repo
        assert (
            cli.main(
                [
                    "init",
                    "--config",
                    str(config_path),
                    "--template",
                    template,
                    "--template-commit",
                    commit,
                ]
            )
            == 0
        )
        return Reconstruction(config_path, workspace, profile, scenario, env)

    return build


def _annotated_tags(draft: Path) -> dict[str, str]:
    """Every tag in the draft repository, mapped to its object type.

    ``git tag -l`` cannot tell an annotated tag from a lightweight one, and
    only an annotated tag carries the revision's own message — so the ledger's
    ``tag_exists`` is satisfied by either and this is what separates them.
    """
    listed = subprocess.run(
        ["git", "-C", str(draft), "tag", "-l"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {
        tag: subprocess.run(
            ["git", "-C", str(draft), "cat-file", "-t", tag],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for tag in listed
    }


def _revision_entries(workspace: Path) -> dict[str, dict]:
    """``revisions.yaml`` as a mapping of tag to body, read off disk."""
    import yaml

    document = yaml.safe_load((workspace / "revisions.yaml").read_text())
    return document["revisions"]


# --- criterion 1: a two-cluster sweep completes ------------------------------


def test_a_two_cluster_sweep_completes(reconstruction, capsys) -> None:
    """The first half of the row: ``run`` sweeps both clusters and exits 0.

    The strict definition of *done* is asserted twice over. Once through
    :func:`ai_rfc.ledger.counts`, which is what every surface reports; and once
    against the three artifacts that definition is made of — the checkpoint
    record, a ``kind: cluster`` revision entry naming the cluster, and an
    **annotated** tag for it — read straight off disk. The second reading is
    not redundant: ``counts`` derives every figure from one ``ClusterState``,
    so a defect in how that state is built would be invisible to a test that
    only asked it what it thought.

    Exit 0 is the whole of the claim rather than a detail of it. A sweep with
    nothing outstanding runs the sweep-end consolidation round and then the
    build gate — ``check --strict``, ``lint --strict``, ``build`` — and none of
    the three is monkeypatched here, so a 0 says the reconstruction the two
    sessions produced passes the first two for real. **``build`` is the
    exception and is worth naming**: ``toolchain_record``'s ``make`` and
    ``kramdown-rfc`` are no-op executables, so ``build: clean`` certifies the
    gate's wiring and the stage's own logic, not that a draft compiled.
    """
    recon = reconstruction("complete", SWEEP_STEPS)

    assert cli.main(["run", "--config", str(recon.config)]) == 0

    states = ledger.clusters(recon.workspace)
    assert ledger.counts(states) == {
        "total": 2,
        "in_window": 2,
        "done": 2,
        "partial": 0,
        "outstanding": 0,
        "pre_seeded": 0,
    }
    entries = _revision_entries(recon.workspace)
    tags = _annotated_tags(recon.workspace / "draft")
    for state in states:
        checkpoint = recon.workspace / "checkpoints" / state.id / "checkpoint.json"
        assert checkpoint.is_file(), state.id
        body = entries[state.revision_tag]
        assert body["kind"] == "cluster" and body["cluster_id"] == state.id
        assert tags[state.revision_tag] == "tag", state.revision_tag
    assert [state.revision_tag for state in states] == [
        "draft-test-fixture-00",
        "draft-test-fixture-01",
    ]

    err = capsys.readouterr().err
    assert "clusters: 2 of 2 done, 0 partial, 0 outstanding" in err
    assert "build: clean" in err
    # `done` is the one stop with no resume line, and printing one would tell
    # the operator to re-run a reconstruction that is finished.
    assert not any(line.startswith("resume:") for line in err.splitlines())


def test_the_sweep_spent_one_session_on_each_cluster(reconstruction) -> None:
    """One session per cluster, then the round that belongs to neither.

    Asserted separately from the ledger because the ledger cannot see it: a
    single session that happened to replay both rounds leaves exactly the same
    two finished clusters, and a driver that never spawned the second would
    then pass the criterion above having done half the work.
    """
    recon = reconstruction("per-cluster", SWEEP_STEPS)

    assert cli.main(["run", "--config", str(recon.config)]) == 0

    rows = recon.sessions(recon.live_run())
    assert [(row["kind"], row["ordinal"]) for row in rows] == [
        ("cluster", 1),
        ("cluster", 2),
        ("consolidation", None),
    ]
    # Asserted twice from two sources, because a row that merely *has* an id
    # is not a row that names its own session: `session_ids` is the whole
    # shared transcript's, in first-appearance order, so an unfiltered `[0]`
    # gives every row the first session's id and still reads as populated.
    transcript, _ = salvage_stream(
        (recon.live_run() / "events.jsonl").read_text(errors="replace")
    )
    launched = [f"fake-{recon.scenario}-{n}" for n in (1, 2, 3)]
    assert session_ids(transcript) == launched
    assert [row["session_id"] for row in rows] == launched
    status = json.loads((recon.live_run() / "status.json").read_text())
    assert status["reason"] == StopReason.done.value
    assert status["sessions"] == 3 and status["timed_out"] is False
    assert status["spent_usd"] == pytest.approx(3 * SESSION_COST)


def test_a_configured_toolchain_that_is_missing_is_refused_before_any_session(
    reconstruction, toolchain_record, capsys
) -> None:
    """A record that is not there must stop the sweep, not be discovered per call.

    The failure this closes is a fail-open one, and it does not look like a
    failure from any single vantage point. ``config.py:642`` defaults
    ``toolchain`` to a path, so it is never None and ``AI_RFC_TOOLCHAIN`` is
    always exported — which makes ``_build_gate``'s "build skipped (no
    toolchain configured)" branch unreachable from a loaded configuration. The
    session then launches perfectly: the MCP server starts and advertises its
    tools, and only each individual *call* comes back
    ``AI_RFC_TOOLCHAIN=… is not a file``. So ``surface_shortfall`` cannot fire,
    the session exits 0 having finished nothing, and the sweep spends an
    attempt per cluster learning it.

    Refused before the first launch instead, naming the command that fixes it.
    The bar is *no run directory at all*: a run directory is minted when the
    first session is about to launch, so its absence is the only evidence that
    nothing was spent.
    """
    recon = reconstruction("no-toolchain", SWEEP_STEPS)
    toolchain_record.unlink()

    assert cli.main(["run", "--config", str(recon.config)]) == 1

    assert recon.runs() == []
    # The deterministic stages run first and are free, idempotent and already
    # reported; what must not have happened is a launch, which is what the
    # absent run directory above says. The refusal itself is asserted as
    # exactly one record: it interpolates a configured path, so it is a
    # line-per-record artifact like every other report line here.
    err = capsys.readouterr().err
    (refusal,) = [line for line in err.splitlines() if line.startswith("error:")]
    assert "ai-rfc toolchain provision" in refusal
    assert str(toolchain_record) in refusal


# --- criterion 2: resume after a kill ----------------------------------------


def _await_session(profile: Path, scenario: str, number: int) -> None:
    """Block until the fake has begun its ``number``-th session of this run.

    The counter is written before the init event and therefore before the
    scenario's sleep, so a kill sent once it reads ``number`` lands inside that
    session's sleep — before it has touched the workspace at all.

    Args:
        profile: ``CLAUDE_CONFIG_DIR``.
        scenario: The run id, which is the counter's filename.
        number: The session to wait for.

    Raises:
        AssertionError: If it has not begun within the launch timeout.
    """
    counter = profile / "fake-sessions" / f"{scenario}.txt"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            if int(counter.read_text()) >= number:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.01)
    raise AssertionError(f"session {number} never started; counter at {counter}")


def test_a_killed_sweep_resumes_without_redoing_the_finished_cluster(
    reconstruction,
) -> None:
    """The second half of the row, and the whole of the resume contract.

    The kill lands in the **second** session's sleep, not the first's, and
    that choice is what makes the spend assertion mean anything: cost reaches
    the transcript only in a result event, so a run killed during its first
    session contributes nothing and "``spent()`` still counts the killed run"
    would hold just as well against a driver that ignored the leftover
    entirely. Killed during the second, the leftover carries the first
    session's ``$0.50`` — and the resumed run has to find it there.

    Three things are asserted of the resume, because three separate mechanisms
    have to agree: the leftover is renamed rather than deleted (its absence of
    a ``status.json`` is the only marker of an interrupted run), the finished
    cluster's checkpoint is byte-identical afterwards, and the new run's own
    ``run.json`` opens at the spend the killed one reached.
    """
    recon = reconstruction("resume", SWEEP_STEPS, sleep=KILL_WINDOW_S)
    first = subprocess.Popen(
        [str(AI_RFC), "run", "--config", str(recon.config)],
        env=recon.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _await_session(recon.profile, recon.scenario, 2)
    os.kill(first.pid, signal.SIGINT)
    first.communicate(timeout=120)

    killed = recon.live_run()
    assert not (killed / record.STATUS_FILE).exists()
    states = {state.ordinal: state.done for state in ledger.clusters(recon.workspace)}
    assert states == {1: True, 2: False}
    assert record.spent(recon.workspace) == pytest.approx(SESSION_COST)
    finished = next(s for s in ledger.clusters(recon.workspace) if s.ordinal == 1)
    frozen = (
        recon.workspace / "checkpoints" / finished.id / "checkpoint.json"
    ).read_bytes()

    second = subprocess.run(
        [str(AI_RFC), "run", "--config", str(recon.config)],
        env=recon.env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert second.returncode == 0, second.stderr
    moved = [p.name for p in recon.runs() if record.INTERRUPTED in p.name]
    assert moved == [f"{killed.name}{record.INTERRUPTED}{sweep.INTERRUPT_CAUSE}"]
    assert (
        recon.workspace / "checkpoints" / finished.id / "checkpoint.json"
    ).read_bytes() == frozen
    rows = recon.sessions(recon.live_run())
    assert [(row["kind"], row["ordinal"]) for row in rows] == [
        ("cluster", 2),
        ("consolidation", None),
    ]
    opened = json.loads((recon.live_run() / "run.json").read_text())
    assert opened["spent_before_usd"] == pytest.approx(SESSION_COST)
    assert record.spent(recon.workspace) == pytest.approx(3 * SESSION_COST)
    assert ledger.counts(ledger.clusters(recon.workspace))["done"] == 2


# --- criterion 3: a budget stop reproduces the resume line --------------------


def test_a_budget_stop_reproduces_its_resume_line(reconstruction, capsys) -> None:
    """The third of the row, held to the standard the row has used throughout.

    The printed line is compared byte for byte with
    :func:`~ai_rfc.driver.stop.resume_line`, and then **parsed back through the
    root parser**. A substring assertion passes just as happily on a line that
    cannot run — a path swallowed by an unquoted space, a bound carried when
    the stop had none — so what is checked is that the line the operator would
    copy resolves to the invocation the stop meant.

    Two clusters at ``$0.50`` against a ``$0.60`` budget stop *after* the
    second session: the row is read before a session launches, so the first
    sees ``$0.00`` spent and the second ``$0.50``. Both clusters therefore
    finish, and the stop is reached at the sweep-end round rather than in the
    middle of the window — which is the shape that proves the budget row
    outranks the consolidation row, not merely that it fires at all.
    """
    recon = reconstruction("budget", SWEEP_STEPS, budget_usd=0.6)

    assert cli.main(["run", "--config", str(recon.config)]) == 1

    lines = capsys.readouterr().err.splitlines()
    assert f"budget: ${2 * SESSION_COST:.2f} of $0.60 spent" in lines
    expected = "resume: " + resume_line(StopReason.budget, recon.config)
    assert expected in lines

    status = json.loads((recon.live_run() / record.STATUS_FILE).read_text())
    assert status["reason"] == StopReason.budget.value
    assert status["exit_code"] == 1
    assert status["spent_usd"] == pytest.approx(2 * SESSION_COST)

    (printed,) = [line for line in lines if line.startswith("resume: ")]
    argv = shlex.split(printed[len("resume: ") :])
    assert argv[0] == cli.PROG
    parsed = cli.build_parser().parse_args(argv[1:])
    assert parsed.verb == "run"
    assert Path(parsed.config) == recon.config
    assert parsed.until is None and parsed.retry is None
