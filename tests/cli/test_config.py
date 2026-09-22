"""recon.yaml: one field table validates, exemplifies and documents itself."""

import re
from pathlib import Path

import pytest
import yaml

from ai_rfc.config import (
    _KINDS,
    FIELDS,
    ConfigError,
    Field,
    _coerce,
    drift,
    dump_config,
    example,
    load_config,
    reference_markdown,
)

pytestmark = pytest.mark.unit

MINIMAL = """\
name: mark
source:
  repo: https://gitlab.cylab.be/cylab/mark
  pin: b901f36095d746ee99dfa85b3d2ad1fbe5f2c533
draft:
  name: draft-elniak-mark-reconstructed
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "recon.yaml"
    path.write_text(text)
    return path


def _key_lines(text: str) -> dict[str, tuple[int, str]]:
    """Locate every mapping key in a generated file by its full dotted path.

    Reads nesting from the indent rather than from the loader, so the assertion
    is about what an operator sees in the file, not about what YAML parses to:
    a key inlined into a flow mapping never reaches this at all.

    Args:
        text: The generated ``recon.yaml``.

    Returns:
        Each key's dotted path, mapped to its line index and its indent.
    """
    stack: list[str] = []
    found: dict[str, tuple[int, str]] = {}
    for index, line in enumerate(text.splitlines()):
        match = re.match(r"^( *)([A-Za-z_][A-Za-z0-9_]*):", line)
        if not match:
            continue
        indent, key = match.groups()
        depth = len(indent) // 2
        stack[depth:] = [key]
        found[".".join(stack)] = (index, indent)
    return found


def test_minimal_config_loads_with_documented_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    config = load_config(_write(tmp_path, MINIMAL))
    assert config.name == "mark"
    assert config.source.host == "gitlab" and config.source.token_env == "GITLAB_TOKEN"
    assert config.workspace == tmp_path / "root" / "reconstructions" / "mark"
    assert config.window is None and config.sessions is None
    assert config.draft.title == "mark: A Reconstructed Specification"
    assert config.draft.rfc_id == "MARK-RECON"
    assert config.toolchain == tmp_path / "root" / "tools" / "toolchain.json"
    assert (
        config.stages.timeline_forge is True and config.stages.views_patches == "span"
    )


def test_every_required_field_is_reported_by_path(tmp_path):
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, "name: mark\n"))
    message = str(excinfo.value)
    assert (
        "source.repo" in message and "source.pin" in message and "draft.name" in message
    )


def test_unknown_keys_and_bad_choices_are_refused(tmp_path):
    with pytest.raises(ConfigError, match="sessions.effort"):
        load_config(
            _write(
                tmp_path, MINIMAL + "sessions:\n  budget_usd: 10\n  effort: extreme\n"
            )
        )
    with pytest.raises(ConfigError, match="source.hots"):
        load_config(
            _write(tmp_path, MINIMAL.replace("  pin:", "  hots: gitlab\n  pin:"))
        )
    with pytest.raises(ConfigError, match="window"):
        load_config(_write(tmp_path, MINIMAL + "window: [5, 2]\n"))
    with pytest.raises(ConfigError, match="draft.name"):
        load_config(_write(tmp_path, MINIMAL.replace("draft-elniak", "elniak")))


@pytest.mark.parametrize(
    "scalar",
    [
        "draft-../../escape",
        "draft-a/b",
        "draft-Upper",
        "draft-",
        "draft-has space",
        # Written as a quoted scalar on purpose: a bare `draft-trailing` with a
        # newline after it is just how YAML ends the line, and the loader hands
        # over a clean string. Only the escape puts the newline *in the value*,
        # which is what a `$`-anchored pattern would wave through.
        '"draft-trailing\\n"',
    ],
    ids=[
        "traversal",
        "separator",
        "uppercase",
        "prefix-only",
        "space",
        "trailing-newline",
    ],
)
def test_a_draft_name_that_is_not_a_draft_name_is_refused(tmp_path, scalar):
    """The prefix is not the shape.

    ``draft.name`` is both a path segment — the scaffold writes
    ``<draft>/<name>.md`` — and the draft's ``docname``, so a prefix check
    alone lets ``draft-../../escape`` name a file outside the workspace. The
    check has to name the whole string.
    """
    with pytest.raises(ConfigError, match="draft.name"):
        load_config(
            _write(tmp_path, MINIMAL.replace("draft-elniak-mark-reconstructed", scalar))
        )


def test_the_draft_names_the_harness_actually_uses_stay_legal(tmp_path, monkeypatch):
    """The shape check must not be stricter than the drafts in this repo."""
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    for name in ("draft-test-fixture", "draft-elniak-mark-reconstructed", "draft-t-01"):
        config = load_config(
            _write(tmp_path, MINIMAL.replace("draft-elniak-mark-reconstructed", name))
        )
        assert config.draft.name == name


def test_sessions_block_requires_a_budget(tmp_path):
    with pytest.raises(ConfigError, match="sessions.budget_usd"):
        load_config(_write(tmp_path, MINIMAL + "sessions:\n  model: claude-opus-5\n"))
    config = load_config(_write(tmp_path, MINIMAL + "sessions:\n  budget_usd: 200\n"))
    assert config.sessions is not None
    assert (
        config.sessions.attempts_per_cluster == 2 and config.sessions.timeout_s == 7200
    )


def test_example_round_trips_and_documents_every_field(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    text = example()
    config = load_config(_write(tmp_path, text))
    assert config.name == "example"
    lines = text.splitlines()
    keys = _key_lines(text)
    for field in FIELDS:
        assert field.doc, field.path
        assert field.path in keys, field.path
        index, indent = keys[field.path]
        assert lines[index - 1] == f"{indent}# {field.doc}", field.path
    reference = reference_markdown()
    for field in FIELDS:
        assert f"`{field.path}`" in reference


def test_an_absent_wall_clock_is_none_and_a_set_one_survives_the_seal(
    tmp_path, monkeypatch
):
    """``sessions.deadline_s`` is optional, and the seal must not lose it.

    Two halves, because the field is read through two doors. The loader has to
    yield ``None`` for an absent optional integer — that is what lets
    ``run`` pass ``deadline=None`` and leave the sweep uncapped — and
    :func:`~ai_rfc.config.dump_config` has to carry a set one, which nothing
    derives: its flat mapping is written by hand, so a field added to the
    dataclass and forgotten there would be silently dropped at ``init`` and
    the sealed copy would sweep without the cap the operator wrote.

    ``MINIMAL`` declares no sessions at all, so
    :func:`test_dump_is_byte_stable_and_reloads` could not have caught it.
    """
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    uncapped = load_config(_write(tmp_path, MINIMAL + "sessions:\n  budget_usd: 200\n"))
    assert uncapped.sessions is not None
    assert uncapped.sessions.deadline_s is None
    assert "deadline_s" not in dump_config(uncapped)

    capped = load_config(
        _write(tmp_path, MINIMAL + "sessions:\n  budget_usd: 200\n  deadline_s: 900\n")
    )
    assert capped.sessions is not None and capped.sessions.deadline_s == 900
    again = load_config(_write(tmp_path, dump_config(capped)))
    assert again.sessions is not None and again.sessions.deadline_s == 900


def test_dump_is_byte_stable_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    config = load_config(_write(tmp_path, MINIMAL + "window: [1, 69]\n"))
    dumped = dump_config(config)
    again = load_config(_write(tmp_path, dumped))
    assert again == config and dump_config(again) == dumped
    assert yaml.safe_load(dumped)["window"] == [1, 69]


def test_drift_refuses_identity_fields_and_notes_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    sealed = load_config(
        _write(tmp_path, MINIMAL + "window: [1, 69]\nsessions:\n  budget_usd: 200\n")
    )
    given = load_config(
        _write(tmp_path, MINIMAL + "window: [1, 70]\nsessions:\n  budget_usd: 250\n")
    )
    refused, noted = drift(sealed, given)
    assert refused == ["window: [1, 69] -> [1, 70]"]
    assert noted == ["sessions.budget_usd: 200.0 -> 250.0"]
    assert drift(sealed, sealed) == ([], [])


def test_an_unknown_key_holding_an_empty_block_is_still_refused(tmp_path):
    """An empty block is where "nothing is silently dropped" was leaking."""
    with pytest.raises(ConfigError, match="bogus: unknown key"):
        load_config(_write(tmp_path, MINIMAL + "bogus: {}\n"))
    with pytest.raises(ConfigError, match="source.bogus: unknown key"):
        load_config(_write(tmp_path, MINIMAL.replace("  pin:", "  bogus: {}\n  pin:")))


def test_an_empty_block_on_a_known_path_stays_legal(tmp_path, monkeypatch):
    """The guard above must not refuse a real section an operator left empty."""
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    config = load_config(_write(tmp_path, MINIMAL + "stages:\n  history: {}\n"))
    assert config.stages.history_cap is None
    assert config.sessions is None


def test_the_kind_table_and_the_coercer_cannot_drift():
    """``_KINDS`` is only worth declaring if both sides are held to it.

    Two directions, because either alone passes while the pair disagrees: no
    field may name a kind the coercer has no branch for, and no declared kind
    may be one the coercer would reject as unknown.
    """
    assert {field.kind for field in FIELDS} <= set(_KINDS)
    for kind in _KINDS:
        with pytest.raises(ConfigError) as excinfo:
            _coerce(Field("probe", kind, "doc"), object())
        assert "unknown field kind" not in str(excinfo.value), kind
    with pytest.raises(ConfigError, match="unknown field kind"):
        _coerce(Field("probe", "nonesuch", "doc"), "x")


def test_a_relative_workspace_anchors_to_the_config_files_own_directory(
    tmp_path, monkeypatch
):
    """Three readers built three different roots out of one relative value.

    ``init`` took ``config.workspace`` raw, so it meant the process's working
    directory; ``doctor`` walked ``config.workspace.parents``, which for a
    relative value is ``[Path('.')]`` and checks no real ancestor at all; the
    server anchored it to the config's own directory. So a config naming
    ``./ws`` named a different tree per caller — and, run from a directory
    holding a ``ws`` of its own, somebody else's tree.

    Anchored once, in the loader, so every reader is handed the same absolute
    path and none of them has to remember to do it.
    """
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    home = tmp_path / "recon"
    home.mkdir()
    path = home / "recon.yaml"
    path.write_text(MINIMAL + "workspace: ./ws\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = load_config(path)

    assert config.workspace.is_absolute()
    assert config.workspace == (home / "ws").resolve()
    # Built from the config's directory, not from the caller's.
    assert config.workspace != (elsewhere / "ws").resolve()
    # And the ancestry `doctor` walks is now a real one.
    assert home.resolve() in config.workspace.parents


def test_an_absolute_workspace_is_left_exactly_as_written(tmp_path, monkeypatch):
    """Anchoring must move the relative case and only the relative case.

    ``Path('/a') / '/b'`` is ``/b``, so joining is already a no-op for an
    absolute value; asserted rather than reasoned, because the sealed bytes
    of every workspace in the tree depend on it.
    """
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    declared = tmp_path / "somewhere" / "else"
    path = _write(tmp_path, MINIMAL + f"workspace: {declared}\n")

    assert load_config(path).workspace == declared
