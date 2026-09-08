"""recon.yaml: one field table validates, exemplifies and documents itself."""

from pathlib import Path

import pytest
import yaml

from ai_rfc.config import (
    FIELDS,
    ConfigError,
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
    for field in FIELDS:
        assert field.doc, field.path
        assert field.path.split(".")[-1] in text, field.path
    reference = reference_markdown()
    for field in FIELDS:
        assert f"`{field.path}`" in reference


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
