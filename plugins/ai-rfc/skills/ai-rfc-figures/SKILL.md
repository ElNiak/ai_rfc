---
name: ai-rfc-figures
description: How to draw and cite ASCII-art figures in a reconstructed Internet-Draft — fenced artwork, width limits, a title, and a caption citing the claims depicted. Use when adding an architecture, sequence or layout figure to the draft.
user-invocable: false
---

# Figures in a reconstructed specification

A figure is prose the reader sees at once; it obeys the same rule as a
sentence: what it asserts, a claim supports.

## Form

- Fence the artwork with `~~~` on its own lines. Plain ASCII, at most 69
  columns, no tabs, no trailing whitespace (the template's lint refuses it).
- Give it an anchor and a title on the line after the closing fence:
  `{: #fig-overview title="Components of the system"}`.
- Draw boxes with `+---+` and `|`, arrows with `--->` and `<---`, and keep
  every label a term from Terminology.

## The caption cites

Within three lines after the closing fence — the attribute line counts as
the first of the three — one sentence states what the figure shows and
cites the claims it depicts, one backticked token per claim, exactly as a
normative sentence would. A figure without such a sentence is a lint
finding. A worked example is in `references/figure-example.md`.

## What not to draw

Nothing the evidence does not support: no boxes for components no claim
names, no arrows for exchanges no claim describes. Structure blocks rendered
by the substrate (bit diagrams, field tables, state tables) are pasted
verbatim and never redrawn by hand.
