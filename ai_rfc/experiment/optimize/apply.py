"""Put a candidate back into the plugin, and leave the diff for a person.

An optimization ends holding one string. Deciding whether to keep it means
reading it as the files the plugin actually ships — under version control,
with the loop skill regenerated the way the repository pins it — so this
module writes exactly those files and stops. Nothing is staged and nothing is
committed: a search that scored well is a proposal, not a verdict, and the
diff is what the proposal is argued from.

The candidate is decoded against the plugin it is being written into, so the
same guards that protect a campaign protect the working tree. A proposal that
dropped a section or a template slot is refused before the first byte lands.

:func:`apply` itself never runs git. The three functions beside it only ask
questions — which paths would be written, which repository they belong to,
which of them already hold uncommitted work — so that a caller can decide to
refuse before calling it, and read the diff after.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..render import TEMPLATE, write_plugin_skill
from .codec import SKILL_DIRS, decode, frontmatters_from_plugin, seed_from_plugin

#: The directory holding the generated loop skill. ``write_plugin_skill`` owns
#: the rendering and only names this path once it has written it, but a caller
#: guarding a working tree has to know what would be overwritten *before*
#: anything is. The two are held together by the test asserting that
#: :func:`targets` names exactly what :func:`apply` writes.
LOOP_SKILL_DIR = "ai-rfc-reconstruction-loop"


@dataclass(frozen=True)
class Applied:
    """What one application wrote.

    Attributes:
        written: Every path written, in write order, ``rendered_skill``
            included, so a caller can diff the whole change from one list.
        rendered_skill: The generated loop SKILL.md, named separately because
            it is the one file the candidate does not carry the text of.
    """

    written: tuple[Path, ...]
    rendered_skill: Path


def targets(plugin_root: Path, *, template_path: Path = TEMPLATE) -> tuple[Path, ...]:
    """Every path :func:`apply` would write, without writing one of them.

    Args:
        plugin_root: The plugin that would be written into.
        template_path: Where the loop template would be written.

    Returns:
        The three prose skills, the template and the generated loop skill, in
        the order :func:`apply` writes them.
    """
    skills = plugin_root / "skills"
    return (
        *(skills / directory / "SKILL.md" for directory in SKILL_DIRS.values()),
        template_path,
        skills / LOOP_SKILL_DIR / "SKILL.md",
    )


def repo_root(path: Path) -> Path | None:
    """The git repository ``path`` sits in.

    Args:
        path: Any path; it need not exist.

    Returns:
        The repository's top level, or None when git reports none — the
        ordinary case for a plugin unpacked outside version control.
    """
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip())


def dirty_paths(repo: Path, paths: Iterable[Path]) -> tuple[str, ...]:
    """Which of ``paths`` hold work that writing over them would destroy.

    Untracked files count: those are the ones git keeps no copy of, so
    overwriting one loses the content outright.

    Args:
        repo: A directory inside the repository to ask.
        paths: The files to ask about.

    Returns:
        Every path git reports as not matching HEAD, in git's order and its
        spelling. Empty when they are all clean — and also empty when git
        refuses, so a caller must establish there is a repository with
        :func:`repo_root` first rather than read this as a passed check.
    """
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain",
            "--",
            *(str(path) for path in paths),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return ()
    return tuple(line[3:] for line in completed.stdout.splitlines() if line.strip())


def apply(
    candidate: str, plugin_root: Path, *, template_path: Path = TEMPLATE
) -> Applied:
    """Write a candidate into the plugin, committing nothing.

    Each prose body is written under the frontmatter its skill already
    carries: a candidate never names a skill or states when to load it. The
    loop is written as the template and the skill is then rendered from that
    file rather than from the text in hand, which is the same path
    ``experiment render`` takes and what the committed skill is pinned to.

    Args:
        candidate: The proposed text, as :func:`~.codec.encode` writes it.
        plugin_root: The plugin to write into. It is also what the candidate
            is decoded against, so drift is measured from the plugin as it
            stands rather than from whatever the campaign began with.
        template_path: Where the loop template is written. The default is the
            packaged template, which is the file the plugin's skill is
            generated from.

    Returns:
        What was written.

    Raises:
        CodecError: If the candidate fails a decode guard, naming every one
            it failed. Nothing has been written in that case.
    """
    bundle = decode(candidate, seed=seed_from_plugin(plugin_root))
    frontmatters = frontmatters_from_plugin(plugin_root)
    written = []
    for section, directory in SKILL_DIRS.items():
        target = plugin_root / "skills" / directory / "SKILL.md"
        body = getattr(bundle, section.replace("-", "_"))
        target.write_text(frontmatters[section] + body)
        written.append(target)
    template_path.write_text(bundle.loop)
    written.append(template_path)
    rendered = write_plugin_skill(plugin_root, template=template_path.read_text())
    written.append(rendered)
    return Applied(written=tuple(written), rendered_skill=rendered)


def diff_stat(repo: Path, paths: Iterable[Path]) -> str:
    """Summarize what changed, for whoever has to review it.

    Args:
        repo: A directory inside the repository to ask.
        paths: The files to limit the diff to.

    Returns:
        What ``git diff --stat`` printed. When git refuses — no repository
        there, or a path belonging to another one — its error text is
        returned instead: the files have already been written, and saying so
        matters more than a diff nobody can produce.
    """
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--stat",
            "--",
            *(str(path) for path in paths),
        ],
        capture_output=True,
        text=True,
    )
    return completed.stdout if completed.returncode == 0 else completed.stderr
