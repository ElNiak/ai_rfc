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
SLOT_RE = re.compile(r"\{\{([a-z_]+)\}\}")
NEUTRAL_TEXTS = (
    ("skills", "ai-rfc-rfc-style", "SKILL.md"),
    ("skills", "ai-rfc-rfc-style", "references", "claim-citation.md"),
    ("skills", "ai-rfc-evidence-hygiene", "SKILL.md"),
)

SKILL_FRONTMATTER = """---
name: ai-rfc-reconstruction-loop
description: The cluster-by-cluster reconstruction driver — read evidence, mine claims, adjudicate, revise the draft, gate, checkpoint, advance. Use when processing timeline clusters of a reconstruction workspace or when asked to continue a reconstruction.
allowed-tools: Read, Grep, Glob, Edit, Write, Bash(python -m panther.plugins.services.testers.ai_rfc*), Bash(git *), Bash(ai_rfc *), Bash(sqlite3 *)
---

"""

_CHURN_SQL = (
    "SELECT path, COUNT(*) c FROM file_changes GROUP BY path ORDER BY c DESC LIMIT 20"
)
_EXPERIMENT_GUIDANCE = (
    "The evidence-hygiene, RFC-style and claim-citation rules follow this "
    "procedure; apply them throughout."
)

_RAW = {
    "cluster_next": (
        "read `$AI_RFC_WORKSPACE/timeline/clusters.jsonl` in ordinal order and take "
        "the first id that has neither a `checkpoints/<id>/` directory nor a "
        "`revisions.yaml` entry"
    ),
    "cluster_get": (
        "read `$AI_RFC_WORKSPACE/clusters/<id>/view.json` (file set, PR number), "
        "`span.diff` (paginate long diffs with `sed -n`) and `evidence/pr.json` "
        "when present"
    ),
    "corpus_query": f'`sqlite3 $AI_RFC_WORKSPACE/corpus/index.sqlite "{_CHURN_SQL}"`',
    "claim_upsert": (
        "edit `$AI_RFC_WORKSPACE/manifest.yaml` by hand — quote every id and "
        "section, never write `status`"
    ),
    "lint": (
        "`python -m panther.plugins.services.testers.ai_rfc "
        "$AI_RFC_WORKSPACE/manifest.yaml --out $AI_RFC_WORKSPACE/out --repo "
        "$AI_RFC_WORKSPACE/clone`"
    ),
    "record_status": (
        "read `$AI_RFC_WORKSPACE/out/report.json` (`claims[]`) and set each claim's "
        "`status` in `manifest.yaml` to exactly its `supported` value"
    ),
    "gate": (
        "`python -m panther.plugins.services.testers.ai_rfc "
        "$AI_RFC_WORKSPACE/manifest.yaml --out $AI_RFC_WORKSPACE/out --repo "
        "$AI_RFC_WORKSPACE/clone --strict`"
    ),
    "checkpoint": (
        "`python -m panther.plugins.services.testers.ai_rfc.draft checkpoint "
        "$AI_RFC_WORKSPACE/manifest.yaml --timeline $AI_RFC_WORKSPACE/timeline "
        "--cluster <id> --out $AI_RFC_WORKSPACE/checkpoints`"
    ),
    "revision_record": (
        "append the entry to `$AI_RFC_WORKSPACE/revisions.yaml` under `revisions:` "
        "(`cluster_id`, `checkpoint_manifest_sha256` copied from the checkpoint's "
        "`checkpoint.json`, `normative_change`, `note`)"
    ),
    "draft_commit": (
        "`git -C $AI_RFC_WORKSPACE/draft add -A && git -C $AI_RFC_WORKSPACE/draft "
        'commit -m "<message>"`'
    ),
    "revision_tag": (
        '`git -C $AI_RFC_WORKSPACE/draft tag -a draft-<name>-NN -m "<message>"` — '
        "only after the strict manifest gate exited 0"
    ),
    "citation_gate": (
        "`python -m panther.plugins.services.testers.ai_rfc.draft gate "
        "$AI_RFC_WORKSPACE/draft --timeline $AI_RFC_WORKSPACE/timeline --checkpoints "
        "$AI_RFC_WORKSPACE/checkpoints --questions $AI_RFC_WORKSPACE/questions.yaml "
        "--revisions $AI_RFC_WORKSPACE/revisions.yaml --out $AI_RFC_WORKSPACE/out "
        "--strict`"
    ),
    "question_draft": (
        "append a `q-NNN` entry (`question`, `claim_ids`, `status: open`, "
        "`asked_at`) to `$AI_RFC_WORKSPACE/questions.yaml`"
    ),
}

SLOT_TABLES: dict[str, dict[str, str]] = {
    "interactive": {
        **_RAW,
        "guidance": (
            "Load `ai-rfc-evidence-hygiene` before touching claims and "
            "`ai-rfc-rfc-style` before touching prose."
        ),
        "preamble": (
            "When the `ai_rfc` MCP server is connected, prefer its tools "
            "(`ai_rfc_cluster_next`, `ai_rfc_claim_upsert`, `ai_rfc_claim_record_status`, "
            "`ai_rfc_checkpoint`, `ai_rfc_gate`, `ai_rfc_revision_tag`, …) or the "
            "equivalent `ai_rfc <verb>` CLI — same core, guardrails enforced up "
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
            "This session has no MCP server and no `ai_rfc` command: drive the "
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
            "This session has no MCP server: drive the workspace with the `ai_rfc` "
            "command through Bash, exactly as written below."
        ),
        "runtime": "`ai_rfc` is on `PATH` and reads both variables",
        "cluster_next": (
            "`ai_rfc cluster-next` (prints the lowest-ordinal cluster with neither "
            "checkpoint nor revision entry, or `null`)"
        ),
        "cluster_get": (
            "`ai_rfc cluster-get <id> --patch` (add `--patch-offset N "
            "--patch-limit N` to page through long diffs)"
        ),
        "corpus_query": f'`ai_rfc corpus-query "{_CHURN_SQL}"`',
        "claim_upsert": (
            "`ai_rfc claim-upsert <id> --text … --section … --level … --layer … "
            '[--field intent=…] [--anchor \'{"evidence_class": "code", '
            '"locator": "…", "commit": "<sha>", "line": N}\']` (repeat '
            "`--anchor`; the verb refuses `status`)"
        ),
        "lint": "`ai_rfc gate` (the linter; fix every entry under `unverified_anchors`)",
        "record_status": (
            "`ai_rfc claim-adjudicate` to see stored vs supported, then "
            "`ai_rfc claim-record-status`"
        ),
        "gate": "`ai_rfc gate --strict`",
        "checkpoint": "`ai_rfc checkpoint <id>`",
        "revision_record": (
            "`ai_rfc revision-record draft-<name>-NN --cluster <id> "
            '--normative|--no-normative --note "…"`'
        ),
        "draft_commit": '`ai_rfc draft-commit -m "<message>"`',
        "revision_tag": (
            '`ai_rfc revision-tag draft-<name>-NN -m "<message>"` — it runs the '
            "strict manifest gate, creates the tag, runs the strict citation gate "
            "and deletes the tag again on findings"
        ),
        "citation_gate": "`ai_rfc citation-gate --strict`",
        "question_draft": (
            '`ai_rfc question-draft "<question quoting the claim text verbatim>" '
            "--claim <id>`"
        ),
    },
    "A": {
        "guidance": _EXPERIMENT_GUIDANCE,
        "preamble": (
            "This session has no shell: drive the workspace with the `ai_rfc_*` MCP "
            "tools, exactly as named below, and edit prose with the Edit/Write "
            "tools."
        ),
        "runtime": "the `ai_rfc` MCP server is connected and reads both variables",
        "cluster_next": (
            "`ai_rfc_cluster_next` (returns the lowest-ordinal cluster with neither "
            "checkpoint nor revision entry, or null)"
        ),
        "cluster_get": (
            "`ai_rfc_cluster_get(cluster_id, include_patch=true)` (page long diffs "
            "with `patch_offset`/`patch_limit`)"
        ),
        "corpus_query": f'`ai_rfc_corpus_query(sql="{_CHURN_SQL}")`',
        "claim_upsert": (
            "`ai_rfc_claim_upsert(claim_id, fields)` with `text`, `section`, "
            "`level`, `layer`, optional `intent`/`req_class`, and `anchors` as a "
            "list of `{evidence_class, locator, commit, line}` (the tool refuses "
            "`status`)"
        ),
        "lint": (
            "`ai_rfc_gate(strict=false)` (the linter; fix every entry under "
            "`unverified_anchors`)"
        ),
        "record_status": (
            "`ai_rfc_claim_adjudicate()` to see stored vs supported, then "
            "`ai_rfc_claim_record_status()`"
        ),
        "gate": "`ai_rfc_gate(strict=true)`",
        "checkpoint": "`ai_rfc_checkpoint(cluster_id)`",
        "revision_record": (
            '`ai_rfc_revision_record(tag="draft-<name>-NN", cluster_id, '
            "normative_change, note)`"
        ),
        "draft_commit": "`ai_rfc_draft_commit(message)`",
        "revision_tag": (
            "`ai_rfc_revision_tag(tag, message)` — it runs the strict manifest gate, "
            "creates the tag, runs the strict citation gate and deletes the tag "
            "again on findings"
        ),
        "citation_gate": "`ai_rfc_citation_gate(strict=true)`",
        "question_draft": (
            '`ai_rfc_question_draft(question="<question quoting the claim text '
            'verbatim>", claim_ids=[<id>])`'
        ),
    },
}


def render_loop(arm: str) -> str:
    """Render the loop template with one invocation table.

    Raises:
        ExperimentError: If ``arm`` has no table or a slot stays unfilled.
    """
    if arm not in SLOT_TABLES:
        raise ExperimentError(
            f"no invocation table for {arm!r}; tables: {', '.join(SLOT_TABLES)}"
        )
    table = SLOT_TABLES[arm]
    template = TEMPLATE.read_text()
    missing = sorted(set(SLOT_RE.findall(template)) - set(table))
    if missing:
        raise ExperimentError(f"table {arm!r} leaves slots unfilled: {missing}")
    return SLOT_RE.sub(lambda match: table[match.group(1)], template)


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
    target = plugin_root / "skills" / "ai-rfc-reconstruction-loop" / "SKILL.md"
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
