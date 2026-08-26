"""Render the reconstruction-loop prompt for the plugin and for each arm.

One template, four invocation tables: ``interactive`` becomes the plugin's
SKILL.md (a test pins the file to this rendering), ``A``/``B``/``C`` become
the appended system prompts of the experiment arms. Every arm prompt is the
rendered loop plus the arm-neutral style and hygiene texts verbatim, so the
arms differ only where a slot names the arm's surface.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from . import ExperimentError

PROMPTS = Path(__file__).parent / "prompts"
TEMPLATE = PROMPTS / "loop.tmpl.md"
SLOT = re.compile(r"\{\{([a-z_]+)\}\}")
NEUTRAL_TEXTS = (
    ("skills", "arfc-rfc-style", "SKILL.md"),
    ("skills", "arfc-rfc-style", "references", "claim-citation.md"),
    ("skills", "arfc-evidence-hygiene", "SKILL.md"),
)

SKILL_FRONTMATTER = """---
name: arfc-reconstruction-loop
description: The cluster-by-cluster reconstruction driver — read evidence, mine claims, adjudicate, revise the draft, gate, checkpoint, advance. Use when processing timeline clusters of a reconstruction workspace or when asked to continue a reconstruction.
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python -m panther.plugins.services.testers.a_rfc*), Bash(git *), Bash(arfc *), Bash(sqlite3 *)
---

"""

_CHURN = (
    "SELECT path, COUNT(*) c FROM file_changes GROUP BY path ORDER BY c DESC LIMIT 20"
)
_EXPERIMENT_GUIDANCE = (
    "The evidence-hygiene, RFC-style and claim-citation rules follow this "
    "procedure; apply them throughout."
)

_RAW = {
    "cluster_next": (
        "read `$ARFC_WORKSPACE/timeline/clusters.jsonl` in ordinal order and take "
        "the first id that has neither a `checkpoints/<id>/` directory nor a "
        "`revisions.yaml` entry"
    ),
    "cluster_get": (
        "read `$ARFC_WORKSPACE/clusters/<id>/view.json` (file set, PR number), "
        "`span.diff` (paginate long diffs with `sed -n`) and `evidence/pr.json` "
        "when present"
    ),
    "corpus_query": f'`sqlite3 $ARFC_WORKSPACE/corpus/index.sqlite "{_CHURN}"`',
    "claim_upsert": (
        "edit `$ARFC_WORKSPACE/manifest.yaml` by hand — quote every id and "
        "section, never write `status`"
    ),
    "lint": (
        "`python -m panther.plugins.services.testers.a_rfc "
        "$ARFC_WORKSPACE/manifest.yaml --out $ARFC_WORKSPACE/out --repo "
        "$ARFC_WORKSPACE/clone`"
    ),
    "record_status": (
        "read `$ARFC_WORKSPACE/out/report.json` (`claims[]`) and set each claim's "
        "`status` in `manifest.yaml` to exactly its `supported` value"
    ),
    "gate": (
        "`python -m panther.plugins.services.testers.a_rfc "
        "$ARFC_WORKSPACE/manifest.yaml --out $ARFC_WORKSPACE/out --repo "
        "$ARFC_WORKSPACE/clone --strict`"
    ),
    "checkpoint": (
        "`python -m panther.plugins.services.testers.a_rfc.draft checkpoint "
        "$ARFC_WORKSPACE/manifest.yaml --timeline $ARFC_WORKSPACE/timeline "
        "--cluster <id> --out $ARFC_WORKSPACE/checkpoints`"
    ),
    "revision_record": (
        "append the entry to `$ARFC_WORKSPACE/revisions.yaml` under `revisions:` "
        "(`cluster_id`, `checkpoint_manifest_sha256` copied from the checkpoint's "
        "`checkpoint.json`, `normative_change`, `note`)"
    ),
    "draft_commit": (
        "`git -C $ARFC_WORKSPACE/draft add -A && git -C $ARFC_WORKSPACE/draft "
        'commit -m "<message>"`'
    ),
    "revision_tag": (
        '`git -C $ARFC_WORKSPACE/draft tag -a draft-<name>-NN -m "<message>"` — '
        "only after the strict manifest gate exited 0"
    ),
    "citation_gate": (
        "`python -m panther.plugins.services.testers.a_rfc.draft gate "
        "$ARFC_WORKSPACE/draft --timeline $ARFC_WORKSPACE/timeline --checkpoints "
        "$ARFC_WORKSPACE/checkpoints --questions $ARFC_WORKSPACE/questions.yaml "
        "--revisions $ARFC_WORKSPACE/revisions.yaml --out $ARFC_WORKSPACE/out "
        "--strict`"
    ),
    "question_draft": (
        "append a `q-NNN` entry (`question`, `claim_ids`, `status: open`, "
        "`asked_at`) to `$ARFC_WORKSPACE/questions.yaml`"
    ),
}

INVOCATIONS: dict[str, dict[str, str]] = {
    "interactive": {
        **_RAW,
        "guidance": (
            "Load `arfc-evidence-hygiene` before touching claims and "
            "`arfc-rfc-style` before touching prose."
        ),
        "preamble": (
            "When the `arfc` MCP server is connected, prefer its tools "
            "(`arfc_cluster_next`, `arfc_claim_upsert`, `arfc_claim_record_status`, "
            "`arfc_checkpoint`, `arfc_gate`, `arfc_revision_tag`, …) or the "
            "equivalent `arfc <verb>` CLI — same core, guardrails enforced up "
            "front (see `docs/parity.md`). The raw substrate commands below "
            "remain the documented fallback and the raw experiment arm."
        ),
        "runtime": (
            "commands below run with an interpreter that imports `panther` "
            "first on `PATH` (`python -m …`)"
        ),
    },
    "C": {
        **_RAW,
        "guidance": _EXPERIMENT_GUIDANCE,
        "preamble": (
            "This session has no MCP server and no `arfc` command: drive the "
            "workspace with the raw substrate commands through Bash, exactly as "
            "written below, and edit YAML by hand where no command exists."
        ),
        "runtime": (
            "`python` on `PATH` imports `panther`; run every `python -m …` "
            "command as written"
        ),
    },
    "B": {
        "guidance": _EXPERIMENT_GUIDANCE,
        "preamble": (
            "This session has no MCP server: drive the workspace with the `arfc` "
            "command through Bash, exactly as written below."
        ),
        "runtime": "`arfc` is on `PATH` and reads both variables",
        "cluster_next": (
            "`arfc cluster-next` (prints the lowest-ordinal cluster with neither "
            "checkpoint nor revision entry, or `null`)"
        ),
        "cluster_get": (
            "`arfc cluster-get <id> --patch` (add `--patch-offset N "
            "--patch-limit N` to page through long diffs)"
        ),
        "corpus_query": f'`arfc corpus-query "{_CHURN}"`',
        "claim_upsert": (
            "`arfc claim-upsert <id> --text … --section … --level … --layer … "
            '[--field intent=…] [--anchor \'{"evidence_class": "code", '
            '"locator": "…", "commit": "<sha>", "line": N}\']` (repeat '
            "`--anchor`; the verb refuses `status`)"
        ),
        "lint": "`arfc gate` (the linter; fix every entry under `unverified_anchors`)",
        "record_status": (
            "`arfc claim-adjudicate` to see stored vs supported, then "
            "`arfc claim-record-status`"
        ),
        "gate": "`arfc gate --strict`",
        "checkpoint": "`arfc checkpoint <id>`",
        "revision_record": (
            "`arfc revision-record draft-<name>-NN --cluster <id> "
            '--normative|--no-normative --note "…"`'
        ),
        "draft_commit": '`arfc draft-commit -m "<message>"`',
        "revision_tag": (
            '`arfc revision-tag draft-<name>-NN -m "<message>"` — it runs the '
            "strict manifest gate, creates the tag, runs the strict citation gate "
            "and deletes the tag again on findings"
        ),
        "citation_gate": "`arfc citation-gate --strict`",
        "question_draft": (
            '`arfc question-draft "<question quoting the claim text verbatim>" '
            "--claim <id>`"
        ),
    },
    "A": {
        "guidance": _EXPERIMENT_GUIDANCE,
        "preamble": (
            "This session has no shell: drive the workspace with the `arfc_*` MCP "
            "tools, exactly as named below, and edit prose with the Edit/Write "
            "tools."
        ),
        "runtime": "the `arfc` MCP server is connected and reads both variables",
        "cluster_next": (
            "`arfc_cluster_next` (returns the lowest-ordinal cluster with neither "
            "checkpoint nor revision entry, or null)"
        ),
        "cluster_get": (
            "`arfc_cluster_get(cluster_id, include_patch=true)` (page long diffs "
            "with `patch_offset`/`patch_limit`)"
        ),
        "corpus_query": f'`arfc_corpus_query(sql="{_CHURN}")`',
        "claim_upsert": (
            "`arfc_claim_upsert(claim_id, fields)` with `text`, `section`, "
            "`level`, `layer`, optional `intent`/`req_class`, and `anchors` as a "
            "list of `{evidence_class, locator, commit, line}` (the tool refuses "
            "`status`)"
        ),
        "lint": (
            "`arfc_gate(strict=false)` (the linter; fix every entry under "
            "`unverified_anchors`)"
        ),
        "record_status": (
            "`arfc_claim_adjudicate()` to see stored vs supported, then "
            "`arfc_claim_record_status()`"
        ),
        "gate": "`arfc_gate(strict=true)`",
        "checkpoint": "`arfc_checkpoint(cluster_id)`",
        "revision_record": (
            '`arfc_revision_record(tag="draft-<name>-NN", cluster_id, '
            "normative_change, note)`"
        ),
        "draft_commit": "`arfc_draft_commit(message)`",
        "revision_tag": (
            "`arfc_revision_tag(tag, message)` — it runs the strict manifest gate, "
            "creates the tag, runs the strict citation gate and deletes the tag "
            "again on findings"
        ),
        "citation_gate": "`arfc_citation_gate(strict=true)`",
        "question_draft": (
            '`arfc_question_draft(question="<question quoting the claim text '
            'verbatim>", claim_ids=[<id>])`'
        ),
    },
}


def render_loop(arm: str) -> str:
    """Render the loop template with one invocation table.

    Raises:
        ExperimentError: If ``arm`` has no table or a slot stays unfilled.
    """
    if arm not in INVOCATIONS:
        raise ExperimentError(
            f"no invocation table for {arm!r}; tables: {', '.join(INVOCATIONS)}"
        )
    table = INVOCATIONS[arm]
    template = TEMPLATE.read_text()
    missing = sorted(set(SLOT.findall(template)) - set(table))
    if missing:
        raise ExperimentError(f"table {arm!r} leaves slots unfilled: {missing}")
    return SLOT.sub(lambda match: table[match.group(1)], template)


def strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block, if any."""
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text if end < 0 else text[end + len("\n---\n") :].lstrip("\n")


def arm_prompt(arm: str, plugin_root: Path) -> str:
    """The appended system prompt of one arm: loop plus the neutral texts."""
    parts = [render_loop(arm)]
    for relative in NEUTRAL_TEXTS:
        parts.append(strip_frontmatter(plugin_root.joinpath(*relative).read_text()))
    return "\n\n".join(part.rstrip("\n") for part in parts) + "\n"


def write_plugin_skill(plugin_root: Path) -> Path:
    """Regenerate the plugin's loop SKILL.md from the interactive table."""
    target = plugin_root / "skills" / "arfc-reconstruction-loop" / "SKILL.md"
    target.write_text(SKILL_FRONTMATTER + render_loop("interactive"))
    return target


def unified_diff(a: str, b: str, a_label: str, b_label: str) -> str:
    """A unified diff of two renderings, labelled for publication."""
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=a_label,
            tofile=b_label,
        )
    )
