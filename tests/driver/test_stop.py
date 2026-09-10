"""Why a sweep stopped, and the line an operator types next.

Two properties carry this module.

*The four session classifications differ in whether they consume an attempt.*
That is the whole reason there are four rather than a boolean, and it is not
hypothetical: MARK's ordinal 38 halted on a $0, one-turn launch error with
$12.62 of its budget still to spend, and D38 recorded that as a budget stop.
Conflating a launch failure with a refusal burns a cluster's retries on a
session that never reached the model, so every classification test below
asserts the *consumption*, not only the label.

*A resume line is copied, so it has to be correct rather than plausible.* The
expected lines are written out literally; a separate test asserts every verb
one names is actually mounted on the root door, and another that a config path
carrying a space or a newline cannot forge a second line.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest

from ai_rfc.driver import DriverError, stop
from ai_rfc.driver.session import SessionResult

CONFIG = Path("/w/recon.yaml")

#: The nine reasons spec §5's state machine can stop on, written out rather
#: than taken from the enum: a test that derived them could not notice the
#: vocabulary shrinking. ``tests/driver/test_record.py`` holds the same nine
#: literals and proves each is a usable ``move_aside`` cause.
NINE = (
    "needs_init",
    "stage_failed",
    "stale_substrate",
    "cluster_halted",
    "budget",
    "wall_clock",
    "surface_shortfall",
    "build_failed",
    "done",
)


def _event(**fields: Any) -> dict[str, Any]:
    """One stream-json result event, with only the fields a test names."""
    return {"type": "result", "session_id": "s1", **fields}


def _session(
    *,
    events: tuple[dict[str, Any], ...] = (),
    timed_out: bool = False,
    exit_code: int | None = 0,
    cost_usd: float = 0.0,
) -> SessionResult:
    """A :class:`SessionResult` carrying only what classification reads.

    The other fields are given plausible values rather than being defaulted
    away: ``classify`` must not depend on them, and a result built by hand from
    a subset of the dataclass would not prove that.
    """
    return SessionResult(
        exit_code=exit_code,
        timed_out=timed_out,
        cost_usd=cost_usd,
        results_seen=len(events),
        session_ids=("s1",),
        wall_s=12.5,
        argv=("claude", "-p"),
        events=events,
        damaged=0,
    )


def _worked() -> SessionResult:
    """A session that ran to the end of its turn and said so."""
    return _session(
        events=(
            _event(subtype="success", is_error=False, num_turns=9, total_cost_usd=1.4),
        ),
        cost_usd=1.4,
    )


def _launch_error() -> SessionResult:
    """D61's session: ``is_error``, one turn, $0 — and budget still left."""
    return _session(
        events=(_event(subtype="error_during_execution", is_error=True, num_turns=1),),
        exit_code=1,
        cost_usd=0.0,
    )


# --- the vocabulary ---------------------------------------------------------


def test_stop_reason_is_exactly_the_nine_the_state_machine_names() -> None:
    """Both directions: a member added or dropped fails this.

    Paired with ``test_record.py``'s grammar test over the same nine literals,
    so a new reason cannot arrive without something proving it is a usable
    directory-name cause.
    """
    assert {reason.value for reason in stop.StopReason} == set(NINE)
    assert {reason.name for reason in stop.StopReason} == set(NINE)


def test_classifications_are_exactly_the_four_spec_5_names() -> None:
    """The four are a closed set; ``classify`` may return nothing else."""
    assert stop.CLASSIFICATIONS == ("refused", "errored", "killed", "budget_hit")


# --- classify(): four different facts about one result event ----------------


def test_a_session_that_worked_is_refused() -> None:
    """The cluster is not done, but the session did reach the model."""
    assert stop.classify(_worked()) == "refused"


def test_a_one_turn_error_is_a_launch_failure_not_a_refusal() -> None:
    """D61: ordinal 38's $0 one-turn error, with $12.62 still to spend."""
    assert stop.classify(_launch_error()) == "errored"


def test_a_killed_session_is_killed_even_with_a_success_in_the_transcript() -> None:
    """A kill leaves the previous session's result as the transcript's tail.

    ``seen`` is 0 here on purpose: the kill is a fact about the process, so it
    must be read before anything in the transcript is.
    """
    killed = _session(
        events=(_event(subtype="success", is_error=False, num_turns=9),),
        timed_out=True,
        exit_code=None,
    )

    assert stop.classify(killed) == "killed"


def test_a_budget_stop_is_budget_hit() -> None:
    """The subtype is the evidence, as ``experiment/runner.py:292`` reads it."""
    hit = _session(
        events=(_event(subtype="error_max_budget", is_error=True, num_turns=7),),
        cost_usd=3.0,
    )

    assert stop.classify(hit) == "budget_hit"


def test_a_budget_stop_on_its_first_turn_is_still_a_budget_stop() -> None:
    """The boundary D38 got wrong, from the other side.

    ``is_error`` with one turn is the launch-failure shape, but a subtype
    naming the budget is not something a launch failure can produce. Read the
    subtype first and both cases stay distinguishable; read the turn count
    first and a budget stop at turn one is filed as an error that consumed
    nothing.
    """
    hit = _session(
        events=(_event(subtype="error_max_budget", is_error=True, num_turns=1),),
    )

    assert stop.classify(hit) == "budget_hit"


def test_classify_reads_this_sessions_result_and_not_the_previous_ones() -> None:
    """The stale-tail hazard, which is D61's defect class exactly.

    A run appends every session to one transcript. A session that died before
    saying anything leaves the *previous* session's success as the tail, and
    classifying on that returns ``refused`` — burning one of the cluster's
    attempts on a session that never reached the model.
    """
    stale_tail = _session(
        events=(_event(subtype="success", is_error=False, num_turns=9),),
        exit_code=1,
    )

    assert stop.classify(stale_tail, seen=1) == "errored"


def test_classify_reads_the_result_seen_names_and_not_merely_a_later_one() -> None:
    """The other half of ``seen``: the slice must be *taken*, not emptied.

    Every other ``seen`` case here leaves the slice empty, so an implementation
    that answered "nothing new" whenever ``seen`` was non-zero would satisfy
    them all — and would report every later session of a run as errored, never
    consuming an attempt and retrying the cluster forever. Here the slice holds
    exactly one event and it is this session's success, so only reading the
    right element gives the right answer.
    """
    second = _session(
        events=(
            _event(subtype="error_during_execution", is_error=True, num_turns=1),
            _event(subtype="success", is_error=False, num_turns=9),
        ),
    )

    classification = stop.classify(second, seen=1)

    assert classification == "refused"
    assert stop.consumes_attempt(classification) is True


def test_a_negative_seen_is_refused_rather_than_slicing_from_the_tail() -> None:
    """``[-1:]`` is a silently wrong answer, not an error: it would read the
    last result event whatever the transcript holds."""
    with pytest.raises(DriverError, match="seen"):
        stop.classify(_worked(), seen=-1)


def test_a_session_whose_result_event_omits_the_turn_count_is_errored() -> None:
    """``num_turns`` is absent when the CLI failed before counting a turn."""
    bare = _session(events=(_event(subtype="error", is_error=True),), exit_code=1)

    assert stop.classify(bare) == "errored"


# --- the attempt-consumption difference, which is why there are four --------


def test_only_a_refusal_consumes_an_attempt() -> None:
    """The literal table. Every value is written out, none derived."""
    assert stop.CONSUMES_ATTEMPT == frozenset({"refused"})
    assert stop.consumes_attempt("refused") is True
    assert stop.consumes_attempt("errored") is False
    assert stop.consumes_attempt("killed") is False
    assert stop.consumes_attempt("budget_hit") is False


def test_a_launch_error_and_a_refusal_differ_in_what_they_cost_the_cluster() -> None:
    """The headline, asserted end to end through ``classify``.

    Same cluster, two sessions, one attempt spent between them. Asserting only
    the two labels would prove nothing about the property the four exist for.
    """
    refusal = stop.classify(_worked())
    error = stop.classify(_launch_error())

    assert stop.consumes_attempt(refusal) is True
    assert stop.consumes_attempt(error) is False
    assert sum(stop.consumes_attempt(c) for c in (refusal, error)) == 1


def test_consumes_attempt_refuses_a_label_outside_the_vocabulary() -> None:
    """A typo must not read as "consumed nothing" — that retries forever."""
    with pytest.raises(DriverError, match="refused"):
        stop.consumes_attempt("refusal")


# --- exit codes -------------------------------------------------------------


def test_done_is_the_only_reason_that_exits_zero() -> None:
    """done 0, stopped with work outstanding 1."""
    assert stop.exit_code(stop.StopReason.done) == 0
    for reason in stop.StopReason:
        if reason is not stop.StopReason.done:
            assert stop.exit_code(reason) == 1


def test_strict_findings_exit_three() -> None:
    """``check --strict`` reports 3, and a sweep that ran it must too."""
    assert stop.exit_code(stop.StopReason.build_failed, strict_findings=True) == 3


def test_strict_findings_is_refused_on_a_reason_that_never_ran_the_gate() -> None:
    """Only the build gate can report strict findings."""
    with pytest.raises(DriverError, match="build_failed"):
        stop.exit_code(stop.StopReason.budget, strict_findings=True)


# --- resume_line() ----------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("needs_init", "ai-rfc init --config /w/recon.yaml"),
        ("stage_failed", "ai-rfc run --config /w/recon.yaml"),
        ("stale_substrate", "ai-rfc status --config /w/recon.yaml"),
        ("budget", "ai-rfc run --config /w/recon.yaml"),
        ("wall_clock", "ai-rfc run --config /w/recon.yaml"),
        ("surface_shortfall", "ai-rfc doctor --config /w/recon.yaml"),
        ("build_failed", "ai-rfc run --config /w/recon.yaml"),
    ],
)
def test_resume_line_reproduces_exactly(reason: str, expected: str) -> None:
    """Written out, not built from the same pieces the implementation uses."""
    assert stop.resume_line(stop.StopReason(reason), CONFIG) == expected


def test_a_halted_cluster_resumes_with_retry() -> None:
    """The one line that carries an id, and the reason ``--retry`` exists."""
    line = stop.resume_line(
        stop.StopReason.cluster_halted,
        CONFIG,
        cluster_id="38",
        known_clusters={"37", "38"},
    )

    assert line == "ai-rfc run --config /w/recon.yaml --retry 38"


def test_nothing_is_resumed_after_a_finished_sweep() -> None:
    """``done`` has no next line; printing one would say to run it again."""
    with pytest.raises(DriverError, match="done"):
        stop.resume_line(stop.StopReason.done, CONFIG)


@pytest.mark.parametrize("cluster_id", ["1", "True", "None", "['a','b']", "38\nrm"])
def test_a_cluster_id_that_names_no_cluster_is_refused(cluster_id: str) -> None:
    """Membership, not a character filter — the ``per_cluster`` precedent.

    Every value here is what YAML's implicit typing hands back for an id an
    agent wrote: ``01`` arrives as ``'1'``, an empty value as ``'None'``, a
    sequence as its repr. None carries a control character, and none names a
    cluster; a filter over characters passes all four.
    """
    with pytest.raises(DriverError, match="cluster"):
        stop.resume_line(
            stop.StopReason.cluster_halted,
            CONFIG,
            cluster_id=cluster_id,
            known_clusters={"37", "38"},
        )


def test_a_halted_cluster_without_the_timeline_is_refused() -> None:
    """Refuse rather than fall back: this module cannot reach the timeline."""
    with pytest.raises(DriverError, match="known"):
        stop.resume_line(stop.StopReason.cluster_halted, CONFIG, cluster_id="38")


def test_a_cluster_id_on_a_reason_that_would_drop_it_is_refused() -> None:
    """Silently dropping it prints a line that resumes the wrong work."""
    with pytest.raises(DriverError, match="cluster"):
        stop.resume_line(
            stop.StopReason.budget,
            CONFIG,
            cluster_id="38",
            known_clusters={"38"},
        )


def test_a_config_path_holding_a_space_stays_one_argument() -> None:
    """The ordinary case: a path with a space is quoted, not broken in two."""
    path = Path("/w/two words/recon.yaml")

    line = stop.resume_line(stop.StopReason.budget, path)

    assert line == "ai-rfc run --config '/w/two words/recon.yaml'"
    assert shlex.split(line) == ["ai-rfc", "run", "--config", str(path)]


#: Every character that breaks or reorders the rendered line. The first three
#: are C0 and DEL; the last five are the ones a C0-and-DEL filter leaks, and
#: for NEL, LS and PS Python's own ``str.splitlines`` already reads the result
#: as two lines. Written out per character rather than as a category, because
#: the category was what the first guard got wrong.
UNPRINTABLE = [
    "\n",  # line feed
    "\r",  # carriage return: rewrites what the terminal shows
    "\x1b",  # escape: opens a control sequence
    "\x85",  # NEL, a line break to str.splitlines
    " ",  # LINE SEPARATOR
    " ",  # PARAGRAPH SEPARATOR
    "‮",  # RIGHT-TO-LEFT OVERRIDE: reorders what is read
    "​",  # ZERO WIDTH SPACE: hides a token boundary
]


@pytest.mark.parametrize("character", UNPRINTABLE)
def test_a_config_path_that_could_not_be_printed_on_a_line_is_refused(
    character: str,
) -> None:
    """Quoting is not enough, which the first spelling of this test assumed.

    ``shlex.quote`` makes a newline *parse* as part of one argument, but the
    printed line still spans two physical lines — and it is the printed line
    that is copied and that the optimize track renders into a prompt a model
    reads, where the second line reads as an instruction of its own.

    C0 and DEL are not the whole class, which the *second* spelling then got
    wrong: NEL, LINE SEPARATOR and PARAGRAPH SEPARATOR break the line too, and
    RLO and ZWSP reorder or hide part of it without breaking it at all.
    """
    with pytest.raises(DriverError, match="line"):
        stop.resume_line(stop.StopReason.budget, Path(f"/w/re{character}con.yaml"))


@pytest.mark.parametrize("character", UNPRINTABLE)
def test_a_known_cluster_id_that_breaks_the_line_is_still_refused(
    character: str,
) -> None:
    """Membership can only be as clean as the set it was handed.

    The timeline those ids are read from is agent-written, so a poisoned id can
    be a legitimate *member* — and then membership alone would pass it straight
    into the line. This is where the leaked characters matter most.
    """
    poisoned = f"38{character}rm -rf /"

    with pytest.raises(DriverError, match="line"):
        stop.resume_line(
            stop.StopReason.cluster_halted,
            CONFIG,
            cluster_id=poisoned,
            known_clusters={poisoned},
        )


def test_an_accented_path_is_not_mistaken_for_an_unprintable_one() -> None:
    """The guard must refuse a class of characters, not everything unfamiliar."""
    path = Path("/w/reconstruction-café/recon.yaml")

    line = stop.resume_line(stop.StopReason.budget, path)

    assert shlex.split(line) == ["ai-rfc", "run", "--config", str(path)]


def test_known_clusters_without_a_cluster_id_is_refused() -> None:
    """The mirror of the case that is already refused.

    A caller that passed the timeline but not the id meant to name a cluster.
    Ignoring the set silently prints a whole-sweep resume line instead.
    """
    with pytest.raises(DriverError, match="cluster"):
        stop.resume_line(stop.StopReason.budget, CONFIG, known_clusters={"38"})


def test_every_resume_line_names_a_verb_the_root_actually_mounts() -> None:
    """Correct, not merely plausible: an unmounted verb is not a resume."""
    from ai_rfc.cli import PROG
    from ai_rfc.entrypoints import ENTRY_POINTS

    verbs = {entry.verb for entry in ENTRY_POINTS}
    for reason in stop.StopReason:
        if reason is stop.StopReason.done:
            continue
        line = (
            stop.resume_line(reason, CONFIG, cluster_id="38", known_clusters={"38"})
            if reason is stop.StopReason.cluster_halted
            else stop.resume_line(reason, CONFIG)
        )
        program, verb, *_ = line.split()
        assert program == PROG
        assert verb in verbs
