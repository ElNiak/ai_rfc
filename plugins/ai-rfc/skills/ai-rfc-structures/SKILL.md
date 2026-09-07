---
name: ai-rfc-structures
description: Use when a cluster defines or changes a wire format, message, record, enumeration or state machine, and the draft needs a figure or table for it.
---

# Structures

A specification a reimplementer can use says what the bytes look like. Declare
those shapes in the manifest and let the tool draw them; never hand-write a
diagram or a field table in the draft.

## When to declare one

Declare a structure when a cluster's evidence pins the shape of something on
the wire or in memory: a packet or frame layout (`wire-format`), a
request/response (`message`), a stored record (`record`), a closed set of
codes (`enum`), or the states a connection moves through (`state-machine`).

Do not declare one for a shape you inferred but cannot cite. Every field,
value and transition names one claim, and a structure is only ever as strong
as its weakest claim — a structure built on a `gap` claim reports as a gap.

## How

1. `ai_rfc_structure_upsert` with the id, the kind, the title, the section it
   belongs to, and its members under the key its kind takes: `fields:` for
   `wire-format`, `message` and `record`, `values:` for `enum`, `states:` and
   `transitions:` for `state-machine`. (The tool's second parameter is named
   `fields`, but it carries the whole body, members included.) Each field,
   value and transition names a `claim:` that must already exist in
   `requirements:`; declare the claim first, and a state is just a name.
2. `ai_rfc_draft_render` returns one block per declared structure, ordered by
   id. Paste each block **verbatim** into the section that structure names,
   delimiters included.
3. Never edit inside the delimiters. The checkpoint freezes those bytes and
   every revision tag compares against them, so a single edited character is a
   gate finding. To change a figure, change the manifest and re-render.

## Widths

`width` is a bit count, or `variable` for a trailing field of unbounded
length. Every `wire-format` field needs one, because it is drawn as a bit
diagram; a `message` or `record` field may omit it. Only the last field of a
structure may be `variable`.

## What not to draw

Architecture and sequence overviews are free-form figures, not structures —
see `ai-rfc-figures`. A structure describes data, not a story.
