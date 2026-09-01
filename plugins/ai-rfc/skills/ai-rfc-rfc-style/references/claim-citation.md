# The claim-citation convention

## Form

A citation is a backticked token: `` `ai_rfc:<claim-id>` `` — for example
`` `ai_rfc:mark:proto.1` ``. The gate extracts citations with the regex
``  `ai_rfc:([^`\s]+)`  `` from the draft file **as committed at each
revision tag**, so only tagged content counts.

The backticks are load-bearing: they keep kramdown-rfc's own `{{ }}` and
`{: }` machinery away from the token, and they render as code in the
produced document, visually separating evidence pointers from prose.

## Dos

- One citation per normative statement, placed at the end of the sentence
  it supports.
- Cite the claim id exactly as it appears in the manifest, including its
  prefix (`mark:proto.1`, not `proto.1`).
- When one paragraph makes several normative statements, cite each.
- Descriptive (accidental) statements cite their claims too — the gate does
  not distinguish; the section placement does.

## Don'ts

- Never cite a claim that is not in the checkpoint manifest paired with the
  revision — the gate names every such citation and `--strict` exits 3.
- Never delete a claim from the manifest to silence a citation finding;
  reconcile the prose or fix the claim.
- Never put a citation inside a heading or the front matter.

## The no-change rule

A revision recorded with `normative_change: false` must have exactly the
same citation set as the previous revision. Adding or removing a citation
IS a normative change; record it as one.
