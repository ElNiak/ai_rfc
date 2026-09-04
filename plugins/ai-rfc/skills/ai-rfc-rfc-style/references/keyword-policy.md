# Keyword policy

A claim's `level` is chosen when the claim is mined and becomes the keyword
of every sentence that cites it. Choose it from the evidence, not from the
fact that the code does something:

| Level | Requires | Example evidence |
|---|---|---|
| MUST / MUST NOT | The implementation *enforces* it: a rejection, an exception, an assertion, a validation error, or a test that fails without it. | `throw new IllegalArgumentException`, a schema check, a test asserting the refusal. |
| SHOULD / SHOULD NOT | A default, a documented recommendation, or a behaviour the code prefers but does not enforce. | A default parameter value; a retry the caller may disable. |
| MAY | An option, an extension point, a behaviour behind a flag. | A configuration key with no default effect; a plugin hook. |

Implementation facts — how a value is computed, which class holds it, what a
mock returns — carry no keyword: they are descriptive, cited, and placed in
Implementation Notes or omitted.

A document in which most keywords are MUST is a mining problem, not a prose
problem: it means "the code does X" was recorded as "the system MUST X". The
lint reports the MUST fraction; treat a fraction above 0.8 as a signal to
re-examine levels, not to reword sentences.
