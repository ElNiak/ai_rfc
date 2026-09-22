# 2. Building the mechanism is not enforcing it

Date: 2026-09-15
Status: Accepted 2026-09-15 · landed 2026-09-22
Related: [ADR 0001, *Threat model for the agent under test*](0001-threat-model-for-the-agent-under-test.md)

## Context

This codebase holds a good principle and states it well. `tests/substrate/test_source_hygiene.py:52`
puts it in a docstring — **"The predicate, not a list"** — and it was learned expensively:
`shlex.quote` and "C0 controls plus DEL" were both enumerations of characters someone thought of,
while `str.isprintable()` is a predicate over a category, and it found nineteen offenders across
codepoints the enumerations missed.

A whole-branch review and the decision map that followed then found the principle failing **seven
times**, in a shape nobody had named. Three independent reviewers each hit one instance; a fourth
turned up compound, at three levels inside a single fix; the fifth came later, from the map's own
path-input ticket; the sixth surfaced only when two reviewers' verdicts on one function were
compared and found to be about different propositions; and **the seventh is not in the codebase at
all** — it is the rule that was supposed to catch the other six.

The shape is not that the principle was forgotten. In every case **the right structure was built**:

- `ai_rfc/cli.py` defines a hardened `Parser` overriding `error()`, and its docstring argues
  correctly that this is **"a funnel, not a list of sites"** — because `error()` is the one method
  every argparse diagnostic routes through.
- `ai_rfc/experiment/markdown.py` was extracted so Markdown primitives could be **shared by every
  renderer** rather than copied.
- `ai_rfc/server/core/__init__.py`'s `diagnostics()` argues, correctly, that a forgery must be fixed
  **where the message is composed** rather than hidden by joining lines at the boundary.
- `ai_rfc/entrypoints.py`'s `ENTRY_POINTS` registry is **provably complete** — 27 declared against 27
  on disk — and kept so by `test_every_cli_module_on_disk_is_registered()`, which derives from the
  tree rather than from a list.

And in every case the property that motivated the structure was **never asserted across the set the
structure already enumerates**:

| Structure built | Set it enumerates | Property never asserted |
|---|---|---|
| The hardened `Parser` / `error()` funnel | the 27 entry points | that each door uses it — **0 of 27 do** |
| `experiment/markdown.py`, shared | renderers in one package | that it covers the sinks that exist — it covers **tables**; the unescaped ones are **code spans** |
| `diagnostics()`'s escaping boundary | the composition sites | that every arm routes through it — **one of two did** |
| `ENTRY_POINTS`, complete and tree-guarded | every CLI module on disk | what that completeness *implies* about those modules |
| **`_checked_cluster_id`, written twice** | cluster ids reaching a path join | that the five joins use it — **none does** |
| **`ledger`'s `in_window`/`pre_seeded` flags and its `counts()` reducer** | every cluster state the ledger hands out | that its consumers honour them — **two recompute raw, and one of those also builds a second set of totals that never reads the ledger at all** |
| **The review mandate** — *process, not code* | the occasions a review is required: after each task, after a major feature, **before merge to main** | that a required review happened — **the before-merge review did not run, and nothing noticed** |

The funnel is real. Twenty-seven doors construct a bare `argparse.ArgumentParser` and walk past it.

> Every file-and-line citation in this section records the tree as it stood on **2026-09-15**. What
> has since closed each row, and where the structures moved, is in *Closures* below; the
> observations are kept at the state they were made in rather than renumbered.

**The sixth is the sharpest of all, because the correct consumer is in the same module.**
`ai_rfc/ledger.py` computes `in_window` and `pre_seeded` on every cluster state it hands out
(`:239-240`), offers a `done` property honouring both (`:88-93`), and consumes them correctly in its
own `counts()` (`:285-292`) — whose docstring states the reason: *"Pre-seeded clusters are excluded
from the in-window figures: they are a baseline's work, so counting them would report progress this
run did not make."* `pipeline/state._checkpoint` (`:236-245`) and `draft/completeness.build`
(`:280-297`) take those same states and recompute raw totals, discarding the flags.

In the other five code instances the structure and its unenforced consumers sit in different files.
Here
the correct reducer is **the module's own public function**, twenty lines from the flags it honours —
and two callers reach past it to recompute. The consequence is measured and operator-facing:
`ai-rfc status` prints the window-blind line directly above the correct one, and
`ai-rfc verify --strict` exits 3 on a finished reconstruction. Adjudicated 2026-09-15 by the decision
map's ticket *Two HOLDS rows a later finding implicates* (the map is gitignored, at
`.superpowers/sdd/2026-09-15-arfc-post-review-rulings/`), whose remedy is this ADR's own: **route the
consumers through the reducer** rather than teach each of them the filter.

**The fifth is the most deliberate, because the authors wrote down what the mechanism defends.**
`_checked_cluster_id` exists in two packages — `driver/stop.py:438` and
`experiment/per_cluster.py:153` — and the first's docstring cites the second by name, so its author
knew of it and wrote a second copy rather than sharing one. Both docstrings state that membership
covers **path escape**: `per_cluster.py:153` says it covers *"a `../..` or an absolute path reaching
a checkpoint path, in one test."*

It is applied at **none** of the five undefended path joins. Meanwhile the weaker sibling guard —
`is_absolute() / ".." in parts` — appears exactly twice, and in `server/core/gates.py` it sits
**thirteen lines from an unguarded join in the same function**: `:84` joins `cluster_id` bare, `:98`
checks `base`. The guard was applied per *parameter that looks like a path* rather than per *segment
that reaches a join*.

**The seventh instance is outside the codebase, and it is the one that let the other six ship.**
Added 2026-09-16. The `superpowers:requesting-code-review` skill enumerates when a review is
required and marks three occasions **Mandatory** — after each task, after a major feature, and
**before merge to main**. That is the same shape as every row above: a structure that enumerates a
set, built for a reason, with the property it exists to produce never asserted over that set. The
skill defines **no record of its own**, and a search of the harness configuration this session could
reach — `~/.claude/hooks/`, `~/.claude/rules/` and `settings.json` — found nothing that records
whether a mandated review happened; the only match was a line enabling the plugin. The whole-branch
review of this roadmap opens by describing itself as *"the review CLI-3's handoff recorded as never
having been run"* — the mandate was on the books, the merge happened, and the gap was visible only
because a later session went looking.

> **Bounded search, not a proof.** The gate and journal machinery in `panther-ivy-plugin` was **not
> searched** — it lives in another worktree this session was isolated from. So "nothing records it"
> is a negative result over a stated scope, and **why the mandate did not fire is undiagnosed**.
> This row claims the shape, not the cause.

**The cost is measured, not hypothetical.** When that review was finally run it produced **3
Critical and 19 Important findings**, including the cross-cutting one this ADR exists for — which
**no per-task review could have produced**, because it is only visible across tasks. CLI-3 carried
per-task reviews for **all eleven of its tasks** (task-1 across three rounds, tasks 2–11 once each),
and none of them saw it. *Scope: eleven is CLI-3's count, verified from its review reports. CLI-1
and CLI-2 kept review **diffs** but no report files, so whether they reviewed per task is not
settled here.*

**It is recorded here deliberately marked as process rather than code**, because generalising a rule
past its evidence is the failure this map caught repeatedly, and a reader is owed the chance to
reject the extension. The claim is narrow: the *shape* is identical, so the *remedy* is the same
one — assert the property over the set the structure already enumerates. The remedy's **form**
differs, since there is no test file to add an assertion to, and **what that form should be is not
decided here**: it is handed to a fresh effort by the decision map's *Out of scope* section (the map
is gitignored, at `.superpowers/sdd/2026-09-15-arfc-post-review-rulings/map.md`), with the diagnosis
left undone on purpose. This row is the observation, not the fix.

## Decision

**When you build a structure that enumerates a set — a registry, a shared module, a funnel — assert
the property that motivated it across that set. A correct structure is not the same as the property
holding.**

Concretely, for the instance that prompted this: `tests/substrate/test_cli_conventions.py` already
parametrizes three behavioural checks over all 27 entry points. **One more assertion** — that each
entry's parser is the hardened `Parser` — closes the whole class with no new machinery. *That
assertion landed on 2026-09-21 at `de77863`, and it did close the class; see Closures.*

## Closures

*Added 2026-09-22.* The owed-fixes row of 2026-09-21 closed seven rows of this shape. Each names the
structure, the set the structure already enumerates, the property that is now asserted over that
set, and the commit that closed it. **Five correspond to instances above** — the first row closes two
of them, because the assertion ranges over `ENTRY_POINTS`, which is what the fourth instance was
asking for. **Two are new**, found while the row was executing. **One instance is still open.**

| Structure | Set it enumerates | Property now asserted | Closed by |
|---|---|---|---|
| The hardened `Parser` and its `error()` funnel — **0 of 27 doors used it** (*instances 1 and 4*) | the 27 entry points, as `ENTRY_POINTS` declares them | that each door's parser **is** the hardened `Parser`: `tests/substrate/test_cli_conventions.py` parametrizes the assertion over `ENTRY_POINTS`, which a separate tree-derived test keeps complete. `Parser` moved to `ai_rfc/parser.py` so a leaf door can construct it without importing the root | `de77863` (Task 8) |
| `experiment/markdown.py`, shared, covering **tables** while the unescaped sinks were **code spans** (*instance 2*) | every value a renderer interpolates into Markdown | that one escaper above the package covers the sinks that exist, code spans included | `9c61fab` + `2c768b5` (Task 4) |
| `diagnostics()`'s escaping boundary — **one composition site of two** (*instance 3*) | the sites where an error or a finding line is composed | that the line is composed where its own message cannot forge it, on every arm | `f714135` + `19a8b84` (Task 4) |
| `_checked_cluster_id`, written twice, applied at **none of the five joins** (*instance 5*) | the cluster ids, workspaces and transcripts that reach a path join | that every join refuses a value which would escape the workspace | `e16bff2` + `fecd60f` (Task 3) |
| The ledger's reducer `counts()`, beside **two consumers recomputing raw** (*instance 6*) | every cluster state the ledger hands out | that both consumers read the reducer rather than rebuild a window-blind total | `d1e34c3` (Task 5) |
| **New.** `_write_once`'s "only if it is not already there" — a **check-then-act** | the run-directory claim and the transcript writes in `driver/` | that the claim is exclusive: an atomic exclusive create rather than a test followed by a write, and a transcript that is never truncated | `ece0a87` (Task 6) |
| **New.** `lifecycle/common.report`'s escaping boundary — **seven sibling `_report` helpers bare** | the stderr boundaries the substrate CLIs print through | that all seven escape at the boundary, the shape the lifecycle verbs already had | `19a8b84` (Task 4) |
| **The review mandate** — *process, not code* (*instance 7*) | the occasions a review is required | **Still open.** Nothing in this row closed it, and nothing here diagnoses why the mandate did not fire; it stays where the map put it, in *Out of scope* | — |

Two things this table is not. It is not a claim that the shape cannot recur: it is a record of the
instances found, and a fresh one turning up is the expected outcome, not a contradiction. And the
two new rows are **not** evidence that the class grew — they were found by looking for it, which is
what naming a shape is for.

## Consequences

**The remedy is usually smaller than it looks.** Three of the six **code** instances need an
assertion and a fourth needs only a redirect to a reducer that already exists — not a redesign,
because the enumeration already exists and is already correct. Reaching for a new structural guard
would duplicate a check the project already has. **The seventh is the exception**: it is process,
there is no test file to add an assertion to, and what its remedy looks like is deliberately left
undecided here. *Borne out: every code row in Closures above was an assertion or a redirect, and no
new structural guard was built for any of them. The seventh is still open.*

**A registry-based assertion is not the enumeration trap, provided the registry is derived.** The
obvious objection — "the parsers someone listed" reproduces exactly the defect — does not apply to
`ENTRY_POINTS`, because a separate tree-derived test fails when a CLI module on disk goes
unregistered. **Derive the set; assert over it.** A hand-maintained list would fail this ADR —
**unless the set is historical**, in which case it must be dated and must name what closed each era,
the one bounded exception set out below.

**This ADR does not replace "the predicate, not a list."** That rule is about choosing the right
*check*. This one is about the gap between having a mechanism and knowing everything goes through it.
They compose: pick a predicate, then assert it over a derived set.

**One kind of set cannot be derived: a historical one.** *Added 2026-09-15 by the decision map's
ticket "What shape the transcript fallback takes" (gitignored, at
`.superpowers/sdd/2026-09-15-arfc-post-review-rulings/`).* The checkpoint-coverage reader must
recognise a checkpoint-write call in **two spellings** — `mcp__arfc__arfc_checkpoint` /
`arfc checkpoint` before the `panther/plugins/services/testers/a_rfc` → `…/ai_rfc` rename, and
`mcp__ai_rfc__ai_rfc_checkpoint` / `ai-rfc checkpoint` after it. Every other set this ADR governs is
derivable from the tree; **no guard can derive a spelling that no longer exists in the source.** So
the rule takes one bounded exception:

> Derive the set where the set is derivable. Where it is **historical**, enumerate it — and **date
> it, naming the event that closed each era**, so the list is auditable against something even
> though it cannot be derived from the source.

That is stricter than an ordinary list rather than laxer. A dated entry naming a rename can be
checked by anyone who can find the rename; an undated list of spellings can be checked against
nothing. The test for this exception is narrow: **the set must be indexed by time, and its earlier
members must be absent from the current tree by construction.** A list that merely *has not been*
derived does not qualify.

*The exception now has exactly one instance in the tree.* `ai_rfc/driver/coverage.py:118` defines
`ALIASES`, two `Era` entries, under a docstring at `:100-117` that names it "the one bounded
exception to ADR 0002" and gives the reason. Era 1 is dated `2026-08-28`–`2026-09-01` and carries
the event that closed it — the `panther/plugins/services/testers/a_rfc` → `…/ai_rfc` rename, PANTHER
`17a99e079` — in the data, not only in a comment. Era 2 takes arm B's command prefix from the arm
profile rather than spelling it, so the live half of the table cannot drift. Landed at `439af29`.

**The cost of not writing it down is measurable.** Seven instances: three found by separate
reviewers who did not know of each other's, one that cost real money by a different route, one that
two reviewers between them had every ingredient to see and neither assembled — and one that is the
**review mandate itself**, whose failure to fire is how the other six reached a merge unreviewed.

## Alternatives considered

- **A convention or checklist** — "at each fix, ask what set this ranges over". Rejected as the sole
  remedy: it depends on someone remembering, which is what failed seven times — the seventh being a
  written mandate that was remembered by nobody at the moment it applied. Kept as the ADR's framing,
  not as the mechanism.
- **A new tree-derived guard per instance.** Rejected as duplicating checks that exist; the project
  already has **eight** test files deriving from the tree.
- **Relocation — make the unhardened path impossible**, via a single parser factory no door can
  bypass. The strongest end state and the largest change, touching all 27 modules. Not rejected on
  merit; deferred as disproportionate to a class that one assertion closes.
