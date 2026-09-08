"""``recon.yaml``: one field table that validates, exemplifies and documents.

The table is the contract. The loader walks it — so every key the file may
hold is named exactly once, an unknown key is an error rather than something
dropped on the floor, and the starter file and the reference page are rendered
from the same rows the loader checks.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_ROOT = "~/ai-rfc-experiments"
IDENTITY_FIELDS = ("source.pin", "window", "draft.name")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_KEY_LINE = re.compile(r"^( *)([A-Za-z_][A-Za-z0-9_]*):")
_KINDS = (
    "str",
    "path",
    "int",
    "float",
    "bool",
    "sha_or_ref",
    "url_or_path",
    "window",
    "str_list",
    "mapping",
)


class ConfigError(ValueError):
    """Raised when ``recon.yaml`` cannot be interpreted as written."""


@dataclass(frozen=True)
class Field:
    """One key of ``recon.yaml``."""

    path: str
    kind: str
    doc: str
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] | None = None
    example: Any = None


FIELDS: tuple[Field, ...] = (
    Field(
        "name",
        "str",
        "Short identifier: lowercase, digits, hyphens. Names the workspace.",
        required=True,
        example="example",
    ),
    Field(
        "workspace",
        "path",
        "Workspace directory; default <experiments root>/reconstructions/<name>.",
        example="~/ai-rfc-experiments/reconstructions/example",
    ),
    Field(
        "source.repo",
        "url_or_path",
        "Repository URL (GitHub or GitLab) or a local clone to copy.",
        required=True,
        example="https://github.com/example/project",
    ),
    Field(
        "source.host",
        "str",
        "Forge kind; inferred from the URL when omitted. "
        "`none` skips the forge snapshot.",
        choices=("github", "gitlab", "none"),
        example="github",
    ),
    Field(
        "source.pin",
        "sha_or_ref",
        "Commit (sha) or ref the reconstruction is pinned to; "
        "init records the resolved sha.",
        required=True,
        example="main",
    ),
    Field(
        "source.token_env",
        "str",
        "Environment variable holding a forge token; default GITHUB_TOKEN or "
        "GITLAB_TOKEN by host. Anonymous fetches work with lower fidelity.",
        example="GITHUB_TOKEN",
    ),
    Field(
        "window",
        "window",
        "Inclusive [low, high] cluster ordinals to reconstruct; default every cluster.",
        example=[1, 10],
    ),
    Field(
        "draft.name",
        "str",
        "Internet-Draft file name without .md; must start with `draft-`.",
        required=True,
        example="draft-yourname-example-reconstructed",
    ),
    Field(
        "draft.title",
        "str",
        "Document title; default `<name>: A Reconstructed Specification`.",
        example="Example: A Reconstructed Specification",
    ),
    Field(
        "draft.abbrev",
        "str",
        "Running-header abbreviation; default `<name> Reconstructed`.",
        example="Example Reconstructed",
    ),
    Field(
        "draft.rfc_id",
        "str",
        "Manifest `rfc:` identifier; default `<NAME>-RECON`.",
        example="EXAMPLE-RECON",
    ),
    Field(
        "draft.author",
        "mapping",
        "Author block: name, org, email. Default: the harness identity.",
        example={"name": "Your Name", "org": "Your Org", "email": "you@example.org"},
    ),
    Field(
        "references",
        "str_list",
        "RFC and Internet-Draft ids the draft may cite; "
        "sealed from the toolchain cache at init.",
        default=(),
        example=["RFC9000", "RFC9114"],
    ),
    Field(
        "sessions.model",
        "str",
        "Model id every session launches against.",
        default="claude-opus-5",
        example="claude-opus-5",
    ),
    Field(
        "sessions.effort",
        "str",
        "Reasoning effort per session.",
        default="high",
        choices=("low", "medium", "high", "xhigh"),
        example="high",
    ),
    Field(
        "sessions.budget_usd",
        "float",
        "Lifetime USD cap for the whole reconstruction; "
        "required when sessions are configured.",
        example=200.0,
    ),
    Field(
        "sessions.timeout_s",
        "int",
        "Seconds before one session's process group is killed.",
        default=7200,
        example=7200,
    ),
    Field(
        "sessions.attempts_per_cluster",
        "int",
        "Sessions a cluster may consume before the sweep halts.",
        default=2,
        example=2,
    ),
    Field(
        "sessions.consolidate_every",
        "int",
        "Run a consolidation round after this many clusters, and at the end.",
        default=10,
        example=10,
    ),
    Field(
        "sessions.profile",
        "path",
        "Isolated Claude Code profile (CLAUDE_CONFIG_DIR); "
        "default <experiments root>/profile.",
        example="~/ai-rfc-experiments/profile",
    ),
    Field(
        "sessions.claude",
        "str",
        "Claude Code binary; resolved to an absolute path at init.",
        default="claude",
        example="claude",
    ),
    Field(
        "toolchain",
        "path",
        "toolchain.json from `ai-rfc toolchain provision`; "
        "default <experiments root>/tools/toolchain.json.",
        example="~/ai-rfc-experiments/tools/toolchain.json",
    ),
    Field(
        "stages.history.cap",
        "int",
        "Per-commit file-row cap for the corpus; default the history stage's own.",
        example=1000,
    ),
    Field(
        "stages.timeline.forge",
        "bool",
        "Enrich the timeline with the forge snapshot.",
        default=True,
        example=True,
    ),
    Field(
        "stages.views.patches",
        "str",
        "Which patches each cluster view carries.",
        default="span",
        choices=("span", "members"),
        example="span",
    ),
    Field(
        "stages.lint.must_fraction_ceiling",
        "float",
        "MUST fraction above which the lint reports a finding.",
        default=0.8,
        example=0.8,
    ),
    Field(
        "stages.build.targets",
        "str_list",
        "Make targets `draft build` runs.",
        default=("txt", "html", "lint", "idnits"),
        example=["txt", "html", "lint", "idnits"],
    ),
    Field(
        "experiment.arms",
        "str_list",
        "Instrument only: arms a campaign runs.",
        example=["A", "B"],
    ),
    Field(
        "experiment.repeats",
        "int",
        "Instrument only: runs per arm.",
        default=2,
        example=2,
    ),
    Field(
        "experiment.seed",
        "int",
        "Instrument only: seed of the frozen run order.",
        default=20260826,
        example=20260826,
    ),
)
_BY_PATH = {f.path: f for f in FIELDS}

#: Every dotted prefix a field hangs under (``source``, ``stages.history``, …).
#: A block at one of these names no keys of its own but is still a real section,
#: which is what tells an empty one apart from a typo.
_PREFIXES = frozenset(
    ".".join(f.path.split(".")[:depth])
    for f in FIELDS
    for depth in range(1, len(f.path.split(".")))
)

for _field in FIELDS:
    if _field.kind not in _KINDS:
        raise ConfigError(
            f"{_field.path}: the field table names unknown kind {_field.kind!r}"
        )


class _BlockMappings(yaml.SafeDumper):
    """A dumper that never inlines a mapping, so every key starts its own line.

    ``example()`` annotates the file it renders by matching keys at the start of
    a line; a mapping YAML is free to inline as ``{a: 1, b: 2}`` would take its
    keys out of reach, silently and only once the values grew short enough.
    """

    def represent_mapping(
        self, tag: str, mapping: Any, flow_style: bool | None = None
    ) -> yaml.MappingNode:
        """Represent ``mapping`` in block style whatever the caller asked for."""
        return super().represent_mapping(tag, mapping, flow_style=False)


def experiments_root() -> Path:
    """Resolve the root that every defaulted path hangs off.

    Returns:
        ``AI_RFC_EXPERIMENTS_ROOT`` when set, else ``~/ai-rfc-experiments``,
        with a leading ``~`` expanded.
    """
    return Path(os.environ.get("AI_RFC_EXPERIMENTS_ROOT", DEFAULT_ROOT)).expanduser()


@dataclass(frozen=True)
class SourceConfig:
    """The repository a reconstruction reads, and the commit it is pinned to."""

    repo: str
    host: str
    pin: str
    token_env: str


@dataclass(frozen=True)
class DraftConfig:
    """The Internet-Draft a reconstruction writes, and its front matter."""

    name: str
    title: str
    abbrev: str
    rfc_id: str
    author: dict[str, str]


@dataclass(frozen=True)
class SessionsConfig:
    """How a reconstruction's model sessions are launched, capped and halted."""

    model: str
    effort: str
    budget_usd: float
    timeout_s: int
    attempts_per_cluster: int
    consolidate_every: int
    profile: Path | None
    claude: str


@dataclass(frozen=True)
class StagesConfig:
    """Per-stage knobs, flattened out of the nested ``stages:`` block."""

    history_cap: int | None
    timeline_forge: bool
    views_patches: str
    lint_must_fraction_ceiling: float
    build_targets: tuple[str, ...]


@dataclass(frozen=True)
class ExperimentConfig:
    """Campaign settings: the instrument's, not one reconstruction's."""

    arms: tuple[str, ...]
    repeats: int
    seed: int


@dataclass(frozen=True)
class ReconConfig:
    """A validated ``recon.yaml``."""

    name: str
    workspace: Path
    source: SourceConfig
    window: tuple[int, int] | None
    draft: DraftConfig
    references: tuple[str, ...]
    sessions: SessionsConfig | None
    toolchain: Path | None
    stages: StagesConfig
    experiment: ExperimentConfig | None
    raw: dict[str, Any] | None = field(default=None, compare=False, repr=False)


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten the document to dotted paths, keeping every key the file wrote.

    Args:
        node: The mapping to walk.
        prefix: The dotted path ``node`` itself sits at.

    Returns:
        One entry per leaf, keyed by dotted path. A block that yields no leaves
        is kept as its own entry unless it names a real section, so that an
        unknown key still reaches the caller when its value is an empty block.
    """
    flat: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if path in _BY_PATH and _BY_PATH[path].kind == "mapping":
                flat[path] = value
            elif isinstance(value, dict):
                nested = _flatten(value, path)
                if nested or path in _PREFIXES:
                    flat.update(nested)
                else:
                    flat[path] = value
            else:
                flat[path] = value
    return flat


def _coerce(field_: Field, value: Any) -> Any:
    kind = field_.kind
    path = field_.path
    if kind == "str":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: expected a non-empty string, got {value!r}")
        if field_.choices and value not in field_.choices:
            raise ConfigError(
                f"{path}: {value!r} is not one of {', '.join(field_.choices)}"
            )
        return value
    if kind == "path":
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{path}: expected a path, got {value!r}")
        return Path(value).expanduser()
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"{path}: expected a non-negative integer, got {value!r}")
        return value
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"{path}: expected a non-negative number, got {value!r}")
        return float(value)
    if kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{path}: expected true or false, got {value!r}")
        return value
    if kind == "sha_or_ref":
        if not isinstance(value, str) or not value.strip() or " " in value:
            raise ConfigError(f"{path}: expected a commit sha or ref, got {value!r}")
        return value
    if kind == "url_or_path":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{path}: expected a URL or path, got {value!r}")
        return value
    if kind == "window":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(
                isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in value
            )
            or value[0] > value[1]
        ):
            raise ConfigError(
                f"{path}: expected [low, high] with 1 <= low <= high, got {value!r}"
            )
        return (value[0], value[1])
    if kind == "str_list":
        if not isinstance(value, list) or not all(
            isinstance(v, str) and v for v in value
        ):
            raise ConfigError(f"{path}: expected a list of strings, got {value!r}")
        return tuple(value)
    if kind == "mapping":
        if not isinstance(value, dict) or not all(
            isinstance(v, str) for v in value.values()
        ):
            raise ConfigError(f"{path}: expected a mapping of strings, got {value!r}")
        return dict(value)
    raise ConfigError(f"{path}: unknown field kind {kind!r}")  # pragma: no cover


def _infer_host(repo: str) -> str:
    netloc = urlparse(repo).netloc.lower()
    if "github" in netloc:
        return "github"
    if "gitlab" in netloc:
        return "gitlab"
    return "none"


def load_config(path: Path) -> ReconConfig:
    """Read and validate ``recon.yaml``.

    Args:
        path: The file to read.

    Returns:
        The validated configuration, defaults applied.

    Raises:
        ConfigError: On an unreadable file, an unknown key, a missing required
            field, a bad value or a bad choice — every problem names its path.
    """
    try:
        document = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ConfigError(f"{path}: {error}") from None
    if not isinstance(document, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    flat = _flatten(document)
    problems: list[str] = []
    for key in sorted(flat):
        if key not in _BY_PATH:
            problems.append(f"{key}: unknown key")
    values: dict[str, Any] = {}
    for field_ in FIELDS:
        if field_.path in flat and flat[field_.path] is not None:
            try:
                values[field_.path] = _coerce(field_, flat[field_.path])
            except ConfigError as error:
                problems.append(str(error))
        elif field_.required:
            problems.append(f"{field_.path}: required")
        else:
            values[field_.path] = field_.default
    has_sessions = any(key.startswith("sessions.") for key in flat)
    if has_sessions and values.get("sessions.budget_usd") is None:
        problems.append("sessions.budget_usd: required when sessions are configured")
    if (
        "draft.name" in values
        and values["draft.name"]
        and not values["draft.name"].startswith("draft-")
    ):
        problems.append("draft.name: must start with `draft-`")
    if "name" in values and values["name"] and not _NAME.match(values["name"]):
        problems.append("name: must match [a-z0-9][a-z0-9-]*")
    if problems:
        raise ConfigError(f"{path}: " + "; ".join(problems))

    name = values["name"]
    root = experiments_root()
    workspace = values["workspace"] or root / "reconstructions" / name
    source = SourceConfig(
        repo=values["source.repo"],
        host=values["source.host"] or _infer_host(values["source.repo"]),
        pin=values["source.pin"],
        token_env=values["source.token_env"]
        or {"github": "GITHUB_TOKEN", "gitlab": "GITLAB_TOKEN"}.get(
            values["source.host"] or _infer_host(values["source.repo"]), ""
        ),
    )
    draft = DraftConfig(
        name=values["draft.name"],
        title=values["draft.title"] or f"{name}: A Reconstructed Specification",
        abbrev=values["draft.abbrev"] or f"{name} Reconstructed",
        rfc_id=values["draft.rfc_id"] or f"{name.upper()}-RECON",
        author=values["draft.author"]
        or {
            "name": "ai-rfc harness",
            "org": "none",
            "email": "ai-rfc-harness@localhost",
        },
    )
    sessions = None
    if has_sessions:
        sessions = SessionsConfig(
            model=values["sessions.model"],
            effort=values["sessions.effort"],
            budget_usd=values["sessions.budget_usd"],
            timeout_s=values["sessions.timeout_s"],
            attempts_per_cluster=values["sessions.attempts_per_cluster"],
            consolidate_every=values["sessions.consolidate_every"],
            profile=values["sessions.profile"],
            claude=values["sessions.claude"],
        )
    stages = StagesConfig(
        history_cap=values["stages.history.cap"],
        timeline_forge=values["stages.timeline.forge"],
        views_patches=values["stages.views.patches"],
        lint_must_fraction_ceiling=values["stages.lint.must_fraction_ceiling"],
        build_targets=tuple(values["stages.build.targets"]),
    )
    experiment = None
    if any(key.startswith("experiment.") for key in flat):
        experiment = ExperimentConfig(
            arms=tuple(values["experiment.arms"] or ()),
            repeats=values["experiment.repeats"],
            seed=values["experiment.seed"],
        )
    toolchain = values["toolchain"] or root / "tools" / "toolchain.json"
    return ReconConfig(
        name=name,
        workspace=workspace,
        source=source,
        window=values["window"],
        draft=draft,
        references=tuple(values["references"]),
        sessions=sessions,
        toolchain=toolchain,
        stages=stages,
        experiment=experiment,
        raw=document,
    )


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    nested: dict[str, Any] = {}
    for path, value in flat.items():
        node = nested
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def dump_config(config: ReconConfig) -> str:
    """Serialise the validated values, byte-stably, in field-table order.

    Args:
        config: The configuration to write.

    Returns:
        YAML holding every value that is set, in ``FIELDS`` order, stable
        enough to seal into a workspace and hash.
    """
    flat: dict[str, Any] = {
        "name": config.name,
        "workspace": str(config.workspace),
        "source.repo": config.source.repo,
        "source.host": config.source.host,
        "source.pin": config.source.pin,
        "source.token_env": config.source.token_env,
        "draft.name": config.draft.name,
        "draft.title": config.draft.title,
        "draft.abbrev": config.draft.abbrev,
        "draft.rfc_id": config.draft.rfc_id,
        "draft.author": dict(config.draft.author),
        "references": list(config.references),
        "toolchain": str(config.toolchain) if config.toolchain else None,
        "stages.history.cap": config.stages.history_cap,
        "stages.timeline.forge": config.stages.timeline_forge,
        "stages.views.patches": config.stages.views_patches,
        "stages.lint.must_fraction_ceiling": config.stages.lint_must_fraction_ceiling,
        "stages.build.targets": list(config.stages.build_targets),
    }
    if config.window is not None:
        flat["window"] = list(config.window)
    if config.sessions is not None:
        s = config.sessions
        flat.update(
            {
                "sessions.model": s.model,
                "sessions.effort": s.effort,
                "sessions.budget_usd": s.budget_usd,
                "sessions.timeout_s": s.timeout_s,
                "sessions.attempts_per_cluster": s.attempts_per_cluster,
                "sessions.consolidate_every": s.consolidate_every,
                "sessions.profile": str(s.profile) if s.profile else None,
                "sessions.claude": s.claude,
            }
        )
    if config.experiment is not None:
        flat.update(
            {
                "experiment.arms": list(config.experiment.arms),
                "experiment.repeats": config.experiment.repeats,
                "experiment.seed": config.experiment.seed,
            }
        )
    ordered = {
        f.path: flat[f.path]
        for f in FIELDS
        if f.path in flat and flat[f.path] is not None
    }
    return yaml.safe_dump(
        _nest(ordered), sort_keys=False, default_flow_style=None, allow_unicode=True
    )


def example() -> str:
    """Render a starter ``recon.yaml``.

    Comments are placed by full path rather than by trailing segment: ``name``
    and ``draft.name`` share a segment, so keying by it puts one field's
    documentation above the other's key.

    Returns:
        Every field carrying its example value, each under its own
        documentation as a comment at the key's own indent.
    """
    lines = [
        "# recon.yaml — one reconstruction, declared."
        " Generated by `ai-rfc config example`."
    ]
    document = _nest({f.path: f.example for f in FIELDS if f.example is not None})
    document["name"] = "example"
    body = yaml.dump(
        document,
        Dumper=_BlockMappings,
        sort_keys=False,
        default_flow_style=None,
        allow_unicode=True,
    )
    docs = {f.path: f.doc for f in FIELDS}
    stack: list[str] = []
    for line in body.splitlines():
        match = _KEY_LINE.match(line)
        if match:
            indent, key = match.groups()
            depth = len(indent) // 2
            stack[depth:] = [key]
            doc = docs.get(".".join(stack))
            if doc:
                lines.append(f"{indent}# {doc}")
        lines.append(line)
    return "\n".join(lines) + "\n"


def reference_markdown() -> str:
    """Render the field table as a Markdown reference page.

    Returns:
        One row per field: its path, kind, whether it is required, its default
        and its documentation.
    """
    rows = [
        "| Field | Kind | Required | Default | Description |",
        "|---|---|---|---|---|",
    ]
    for f in FIELDS:
        default = "" if f.default is None else f"`{f.default!r}`"
        choices = f" One of: {', '.join(f.choices)}." if f.choices else ""
        rows.append(
            f"| `{f.path}` | {f.kind} | {'yes' if f.required else 'no'} "
            f"| {default} | {f.doc}{choices} |"
        )
    return "\n".join(rows) + "\n"


def drift(sealed: ReconConfig, given: ReconConfig) -> tuple[list[str], list[str]]:
    """Compare a sealed config with the file as given now.

    Args:
        sealed: What ``init`` sealed into the workspace.
        given: What the operator passed this time.

    Returns:
        ``(refused, noted)``: identity fields that changed and must be refused
        (D57), and every other change, as ``path: old -> new`` lines.
    """
    before = _flatten(yaml.safe_load(dump_config(sealed)))
    after = _flatten(yaml.safe_load(dump_config(given)))
    refused: list[str] = []
    noted: list[str] = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            line = f"{path}: {before.get(path)!r} -> {after.get(path)!r}".replace(
                "'", ""
            )
            (refused if path in IDENTITY_FIELDS else noted).append(line)
    return refused, noted
