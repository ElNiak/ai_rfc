# SP7b — the structures registry and the block freeze, proved

Everything below was measured on 2026-09-07 against ai_rfc `8246a15`, the head
of SP7b's Tasks 1–9. The plan is `2026-09-03-arfc-draft-quality-sp7b`; the
protocol amendment it records is `docs/experiment-protocol.md`, subsection
"2026-09-05 — draft quality v2, SP7b".

The claim this document supports is narrow and mechanical: a structure declared
in the manifest is rendered by the tool, and a revision tag catches any drift
between what was rendered and what the draft actually carries. A faithful paste
gates clean; a single changed character does not.

## The whole suite

```
$ SSLKEYLOGFILE= python -m pytest tests -n auto -p no:cacheprovider
1281 passed, 11 skipped in 177.10s (0:02:57)
```

Zero failed. The SP7b baseline (plan Task 0, ai_rfc `07a02fb`) was 1118 passed
+ 10 skipped; the growth is this plan's own tests plus the peer session's.

## The MARK draft did not regress

The MARK manifest declares no structures, so every check SP7b added must be a
no-op on it. Run on a writable copy of the sealed baseline
(`~/ai-rfc-experiments/baselines/mark-a1-2026-09-03/workspace`), never on the
baseline or the campaign directory:

```
$ python -m ai_rfc.draft gate /tmp/claude/sp7b/mark-b/draft \
  --timeline /tmp/claude/sp7b/mark-b/timeline \
  --checkpoints /tmp/claude/sp7b/mark-b/checkpoints \
  --questions /tmp/claude/sp7b/mark-b/questions.yaml \
  --revisions /tmp/claude/sp7b/mark-b/revisions.yaml \
  --out /tmp/claude/sp7b/mark-b/out --strict
note: gate clean
exit=0
```

`gate-report.json`: `{"findings": []}`.

The SP7a baseline recorded for the same draft
(`docs/experiments/2026-09-03-sp7a-mark-baseline.md`, section
"`draft gate --strict`") is `note: gate clean`, `exit=0`, `{"findings": []}`.
The two are **identical**: same note, same exit code, same empty findings list.
The defaulted revision `kind` and the new block scan changed nothing for a
structure-free draft, which is the property SP7b had to preserve.

## The round trip, both directions

```
$ SSLKEYLOGFILE= python -m pytest tests/substrate/draft/test_gate.py \
  -k "untouched_structured or one_byte" -v
tests/substrate/draft/test_gate.py::test_a_one_byte_edit_to_a_rendered_block_is_a_finding PASSED
tests/substrate/draft/test_gate.py::test_an_untouched_structured_draft_gates_clean PASSED
2 passed, 28 deselected in 1.74s
```

These two are the gate of the plan, and they are only meaningful as a pair.
`test_an_untouched_structured_draft_gates_clean` walks the whole loop the agent
walks — declare a structure, render it, paste the blocks, checkpoint, tag, gate
— and shows a faithful paste produces no finding. Without it, a gate that
flagged everything would also "pass" the tamper test.
`test_a_one_byte_edit_to_a_rendered_block_is_a_finding` changes one character
inside a pasted block (`uint8` → `uint9`) and shows the gate reports it, naming
the block that no longer matches the frozen rendering. Turning that finding
into a refused tag is the server's `revision_tag` behaviour, not this test's.
Without it, a gate that never looked at the blocks would pass the clean test.

## What the tool draws

The five renderings below are the byte-exact goldens under
`tests/substrate/draft/goldens/`, copied verbatim, one per structure kind.
`test_each_kind_matches_its_golden` (`tests/substrate/draft/test_structures.py`)
asserts the renderer reproduces each of them exactly, and fails loudly if one
changes. The gate compares each tag against its own checkpoint's frozen
rendering and never renders anything itself, so a changed renderer leaves every
already-tagged revision gating clean; what a change moves is the next
checkpoint, which freezes the new bytes against a draft still carrying the old
paste, and the lint, which renders live and would call every pasted block
stale. They are reproduced here so a reader can see the output without running
the suite.

Each block is delimited by `{::comment}` markers naming the structure id. The
gate reads a draft's blocks with the same parser the lint uses, and compares
each body against the one frozen in the tag's checkpoint.

### `wire-format`

```
{::comment}
ai_rfc:struct:header begin
{:/comment}
**Message header**

~~~
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|Version|  Type |                     Length                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Payload (variable)                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
~~~

| Field | Bits | Description | Claim |
|---|---|---|---|
| Version | 4 | Protocol version. | `ai_rfc:spec:1.1` |
| Type | 4 | - | `ai_rfc:spec:1.2` |
| Length | 24 | - | `ai_rfc:spec:1.3` |
| Payload | variable | - | `ai_rfc:spec:1.4` |
{::comment}
ai_rfc:struct:header end
{:/comment}
```

A last field may be declared `variable` rather than a bit count, as `Payload`
is here. Every row names the claim it rests on, and a field whose description
is unstated renders `-` rather than inventing prose.

### `message`

```
{::comment}
ai_rfc:struct:hello begin
{:/comment}
**Hello**

| Field | Type | Size | Description | Claim |
|---|---|---|---|---|
| token | opaque | 64 | Session token. | `ai_rfc:spec:3.1` |
{::comment}
ai_rfc:struct:hello end
{:/comment}
```

### `record`

```
{::comment}
ai_rfc:struct:entry begin
{:/comment}
**Log entry**

| Field | Type | Size | Description | Claim |
|---|---|---|---|---|
| stamp | uint64 | - | Milliseconds. | `ai_rfc:spec:7.1` |
{::comment}
ai_rfc:struct:entry end
{:/comment}
```

`message` and `record` share the field table; they differ in what they claim to
describe, not in how they draw it.

### `enum`

```
{::comment}
ai_rfc:struct:codes begin
{:/comment}
**Error codes**

| Value | Name | Description | Claim |
|---|---|---|---|
| 0x00 | NO_ERROR | Normal close. | `ai_rfc:spec:6.1` |
| 0x01 | PROTO | - | `ai_rfc:spec:6.2` |
{::comment}
ai_rfc:struct:codes end
{:/comment}
```

### `state-machine`

```
{::comment}
ai_rfc:struct:conn begin
{:/comment}
**Connection lifecycle**

~~~
+------+
| idle |
+------+
+------+
| open |
+------+
+--------+
| closed |
+--------+
idle --connect--> open
open --timeout--> closed
~~~

| From | Event | Guard | To | Claim |
|---|---|---|---|---|
| idle | connect | - | open | `ai_rfc:spec:5.1` |
| open | timeout | no traffic | closed | `ai_rfc:spec:5.2` |
{::comment}
ai_rfc:struct:conn end
{:/comment}
```

A state machine is the one kind whose members are split: `states` are named
without evidence, and each `transitions` entry names the claim behind it. So a
transition carries a claim and a bare state does not, and a structure's status
is the minimum over its transitions.

## What this does and does not establish

It establishes that the renderer is deterministic against five frozen goldens,
that a faithful paste gates clean, that a one-character edit to a pasted block
is caught, and that a structure-free draft gates exactly as it did before SP7b.

It does not establish anything about an agent's behaviour: no model was run.
Whether an agent actually uses `ai_rfc_draft_render` rather than hand-drawing a
figure is a question for a campaign, not for this suite. The consolidation
round and its prompts are SP7c's; the instrument and the paid runs are SP7d's.

## Addendum — 2026-09-08, re-measured at `828afa4`

The numbers above were taken at `8246a15`. Before the branch shipped, a review
of the whole of SP7b found that a newline inside any structure member's text
reached the rendered block unescaped: a description carrying the three-line
delimiter sequence closed and reopened its block from inside, the parser
reported nothing, and a tampered paste gated clean and linted clean. The fix
wave `3fa166f..828afa4` (five commits) collapses all whitespace at every point
where author text enters a block, reports a repeated block id and a malformed
frozen rendering as findings, and anchors the structure-id validator with `\Z`
so an id cannot carry a trailing newline into a marker. Everything this
document claims was re-measured at the head that ships:

```
$ SSLKEYLOGFILE= python -m pytest tests -n auto -p no:cacheprovider
1300 passed, 11 skipped in 118.86s (0:01:58)
```

```
$ python -m ai_rfc.draft gate /tmp/claude/sp7b/mark-b/draft … --strict
note: gate clean
exit=0
```

`gate-report.json`: `{"findings": []}`, 32.9 s wall — still identical to the
SP7a record.

```
$ SSLKEYLOGFILE= python -m pytest tests/substrate/draft/test_gate.py \
  -k "untouched_structured or one_byte" -v
tests/substrate/draft/test_gate.py::test_a_one_byte_edit_to_a_rendered_block_is_a_finding PASSED
tests/substrate/draft/test_gate.py::test_an_untouched_structured_draft_gates_clean PASSED
2 passed, 30 deselected in 0.91s
```

The five goldens are byte-identical before and after the wave: the collapse
changes only what a member containing a line terminator renders as, and no
golden member contains one.
