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

#: Every reason spec §5's state machine can stop on, written out rather than
#: taken from the enum: a test that derived them could not notice the
#: vocabulary shrinking. ``tests/driver/test_record.py`` holds the same
#: literals and proves each is a usable ``move_aside`` cause.
#:
#: Nine of them are spec §5's own rows. ``session_failed`` and
#: ``consolidation_failed`` were added by Task 10, which found two stopping
#: events the nine could not name: spec §5 says an *errored* session (a launch
#: or API failure) "stops with the resume line", and D59 says a failing
#: sweep-end consolidation exits 1. Neither is a cluster reaching its attempt
#: cap, a deterministic stage exiting non-zero, or a build gate finding, so
#: mapping either onto an existing member would have printed a resume line
#: that fixes something else. Task 11 added ``bound_reached`` and Task 12
#: ``action_performed`` — the two ways a sweep stops because the *operator*
#: asked it to stop there rather than because anything went wrong.
REASONS = (
    "needs_init",
    "stage_failed",
    "stale_substrate",
    "cluster_halted",
    "budget",
    "wall_clock",
    "surface_shortfall",
    "session_failed",
    "consolidation_failed",
    "build_failed",
    "bound_reached",
    "action_performed",
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


def test_stop_reason_is_exactly_the_reasons_the_state_machine_names() -> None:
    """Both directions: a member added or dropped fails this.

    Paired with ``test_record.py``'s grammar test over the same literals, so a
    new reason cannot arrive without something proving it is a usable
    directory-name cause.
    """
    assert {reason.value for reason in stop.StopReason} == set(REASONS)
    assert {reason.name for reason in stop.StopReason} == set(REASONS)


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


def test_the_three_reasons_that_exit_zero_are_done_bound_and_one_action() -> None:
    """done 0, a bound honoured 0, one action performed 0; anything else 1.

    The three zeroes are written out rather than read off a set in the
    implementation, and every other member is asserted 1 by exclusion: a
    fourth reason quietly joining the zero set fails the loop below.

    Renamed rather than patched when ``action_performed`` joined, for the
    reason Task 11 renamed it when ``bound_reached`` did: a test whose *name*
    asserts a membership it no longer checks is worse than a stale comment,
    because the name is what a reader trusts without opening the body.

    ``action_performed`` exits 0 on the same reading of spec §5 that put
    ``bound_reached`` there: "stopped with work outstanding 1" means *could
    not continue*, not *work exists*. A ``next`` that performed its one action
    leaves the rest of the window outstanding by design, exactly as a bounded
    ``run`` does, and exactly as the no-sessions boundary stop — which returns
    0 with every cluster outstanding — already did.
    """
    zero = (
        stop.StopReason.done,
        stop.StopReason.bound_reached,
        stop.StopReason.action_performed,
    )
    assert stop.exit_code(stop.StopReason.done) == 0
    assert stop.exit_code(stop.StopReason.bound_reached) == 0
    assert stop.exit_code(stop.StopReason.action_performed) == 0
    for reason in stop.StopReason:
        if reason not in zero:
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
        ("session_failed", "ai-rfc run --config /w/recon.yaml"),
        ("consolidation_failed", "ai-rfc run --config /w/recon.yaml"),
        ("build_failed", "ai-rfc run --config /w/recon.yaml"),
        ("bound_reached", "ai-rfc run --config /w/recon.yaml"),
        # The one line in this table that does not say ``run``: only
        # ``mode="one"`` mints this reason, and the operator who asked for one
        # action resumes by asking for the next one.
        ("action_performed", "ai-rfc next --config /w/recon.yaml"),
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


# --- the bound a stop was carrying ------------------------------------------


@pytest.mark.parametrize("until", ["views", "cluster:c3", "ordinal:4"])
def test_a_stop_that_had_a_bound_resumes_with_it(until: str) -> None:
    """D59 says the **exact** resume line, and a dropped bound is not exact.

    ``run --until cluster:c3`` that stops on the budget prints a line which,
    copied, sweeps past c3 — it silently does more than the operator asked
    for. Written out as whole lines rather than assembled from the same pieces
    the implementation uses.

    ``budget`` rather than ``bound_reached``, which is the one reason that
    must *not* carry its bound: see the pair of tests below.
    """
    line = stop.resume_line(stop.StopReason.budget, CONFIG, until=until)

    assert line == f"ai-rfc run --config /w/recon.yaml --until {until}"


def test_the_bound_on_a_resume_line_parses_back_to_the_same_bound() -> None:
    """The line is argv, so the test is a round trip rather than a substring.

    A cluster id may hold a space — ``_bound`` refuses only what is
    unprintable — and unquoted such a bound becomes two argv words. Only
    feeding the emitted string back through the root parser proves the line
    an operator copies reconstructs the bound they gave.
    """
    from ai_rfc.cli import build_parser

    line = stop.resume_line(stop.StopReason.budget, CONFIG, until="cluster:c 3")

    args = build_parser().parse_args(shlex.split(line)[1:])
    assert args.until == "cluster:c 3"


def test_a_reached_bound_is_dropped_because_the_line_would_re_achieve_it() -> None:
    """``bound_reached`` is the one stop whose bound is **already satisfied**.

    Carrying it made the resume line a *fixed point*: the same workspace run
    twice with ``--until cluster:c1`` printed byte-identical output, launched
    nothing and exited 0 both times. That is the very thing ``resume_line``
    refuses to print for ``done`` — "a line telling the operator to run it
    again is worse than none" — so for this reason the exact resume is the
    sweep **without** the bound, which is what "continue from here" means.

    A line is still printed: D59 requires one from every stop, and only
    ``done`` is exempt.
    """
    line = stop.resume_line(stop.StopReason.bound_reached, CONFIG, until="cluster:c3")

    assert line == "ai-rfc run --config /w/recon.yaml"


def test_one_action_keeps_its_bound_because_it_did_not_satisfy_it() -> None:
    """The distinction the two members now turn on, asserted as a pair.

    A single action does not reach a bound — ``next --until ordinal:9`` that
    performed one session is nowhere near ordinal 9 — so re-issuing the bound
    is exactly right here, and dropping it would let the *next* step run past
    the place the operator asked it to stop.
    """
    line = stop.resume_line(stop.StopReason.action_performed, CONFIG, until="ordinal:9")

    assert line == "ai-rfc next --config /w/recon.yaml --until ordinal:9"


def test_the_two_operator_asked_stops_differ_only_in_whether_they_re_issue() -> None:
    """Round-tripped as argv, because the difference is what the line *does*.

    Both stops are the operator's own; only one of them has already got where
    it was going. Parsed back rather than compared as strings, so what is
    pinned is the bound the copied command would impose.
    """
    from ai_rfc.cli import build_parser

    reached = build_parser().parse_args(
        shlex.split(
            stop.resume_line(stop.StopReason.bound_reached, CONFIG, until="ordinal:9")
        )[1:]
    )
    performed = build_parser().parse_args(
        shlex.split(
            stop.resume_line(
                stop.StopReason.action_performed, CONFIG, until="ordinal:9"
            )
        )[1:]
    )

    assert reached.until is None
    assert performed.until == "ordinal:9"


def test_a_bound_is_dropped_for_a_verb_that_cannot_take_one() -> None:
    """``init``, ``status`` and ``doctor`` have no ``--until``.

    So a bound on their line would be an argument the verb refuses — the very
    defect the spaced path was. Dropped rather than refused: a
    ``run --until cluster:c1`` against an unpinned workspace is a real state,
    and answering it with "no resume line could be rendered" would replace a
    working instruction with none.
    """
    for reason in (
        stop.StopReason.needs_init,
        stop.StopReason.stale_substrate,
        stop.StopReason.surface_shortfall,
    ):
        line = stop.resume_line(reason, CONFIG, until="cluster:c3")
        assert "--until" not in line


def test_stepping_resumes_with_next_rather_than_run() -> None:
    """``ai-rfc next`` must not hand a one-step operator a full sweep.

    The reason knows the *stop*; only the caller knows the cadence. ``run`` is
    the one verb rewritten: ``init``, ``status`` and ``doctor`` are places
    ``next`` would stop again exactly as ``run`` would, so rewriting them
    would send the operator somewhere that cannot help.
    """
    assert (
        stop.resume_line(stop.StopReason.budget, CONFIG, stepping=True)
        == "ai-rfc next --config /w/recon.yaml"
    )
    assert (
        stop.resume_line(stop.StopReason.needs_init, CONFIG, stepping=True)
        == "ai-rfc init --config /w/recon.yaml"
    )
    assert (
        stop.resume_line(stop.StopReason.surface_shortfall, CONFIG, stepping=True)
        == "ai-rfc doctor --config /w/recon.yaml"
    )


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
