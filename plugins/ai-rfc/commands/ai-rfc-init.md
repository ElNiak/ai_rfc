---
description: Initialize a reconstruction workspace — clone, corpus, forge snapshot, timeline, views, scaffolded draft
---

Initialize the reconstruction workspace at `$AI_RFC_WORKSPACE` for the target
repository given as `$ARGUMENTS` (a forge URL). `PANTHER_REPO` and
`AI_RFC_WORKSPACE` must be set; stop with a clear message if either is
missing or the workspace already holds a corpus.

Run each stage from `$PANTHER_REPO`, with `$PY` an interpreter that imports
`panther`:

1. **Clone at full depth** into `$AI_RFC_WORKSPACE/clone` (shallow clones are
   refused by extraction). Record `git rev-parse HEAD` — this is the pin
   everything else is verified against.
2. **Corpus**: `$PY -m panther.plugins.services.testers.ai_rfc.history
   $AI_RFC_WORKSPACE/clone --out $AI_RFC_WORKSPACE/corpus`.
3. **Forge snapshot**: `$PY -m panther.plugins.services.testers.ai_rfc.forge
   <URL> --repo $AI_RFC_WORKSPACE/clone --out $AI_RFC_WORKSPACE/forge` with
   `GITHUB_TOKEN`/`GITLAB_TOKEN` exported (`gh auth token` can supply the
   GitHub one). A fetch against a self-hosted GitLab requires the user's
   explicit go-ahead first. On failure, continue git-only and say so.
4. **Timeline**: `$PY -m panther.plugins.services.testers.ai_rfc.timeline
   $AI_RFC_WORKSPACE/corpus --repo $AI_RFC_WORKSPACE/clone --out
   $AI_RFC_WORKSPACE/timeline`, adding `--forge <snapshot dir>` when step 3
   produced one. Report the cluster/rescue/unmatched numbers.
5. **Views**: `$PY -m panther.plugins.services.testers.ai_rfc.views
   $AI_RFC_WORKSPACE/timeline --corpus $AI_RFC_WORKSPACE/corpus --repo
   $AI_RFC_WORKSPACE/clone --out $AI_RFC_WORKSPACE/clusters` plus `--forge
   <snapshot>` when available.
6. **Draft scaffold**: clone `https://github.com/ElNiak/auto-i-d-template`
   into `$AI_RFC_WORKSPACE/draft`, remove its `.git`, `git init -b main`,
   and **delete the `draft-*` rule from its root `.gitignore`** (the
   template repo ignores draft files; a draft repo must not). Create
   `draft-<name>.md` from the template's example with real front matter.
   Commit the scaffold.
7. **Registers**: write an empty `questions.yaml` (`questions: {}`) and
   `revisions.yaml` (`revisions: {}`), and create `interviews/`.

Finish by reporting the pin, the cluster counts, and the first cluster id
the loop will process.
