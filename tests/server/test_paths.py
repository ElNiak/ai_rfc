"""The environment contract: a required config, and an optional toolchain."""

import shutil

import pytest

from ai_rfc.server.paths import EnvError, resolve_context


def _seal(root, declares=None):
    """Write the two files ``init`` writes, and return the sealed config's path.

    Args:
        root: Directory to seal; created if absent.
        declares: Extra top-level YAML lines, already formatted.

    Returns:
        The path of the sealed ``recon.yaml``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "init.json").write_text("{}")
    config = root / "recon.yaml"
    config.write_text(
        "name: demo\n"
        f"workspace: {root}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n" + (declares or "")
    )
    return config


def test_the_contract_needs_only_the_config(tmp_path, monkeypatch):
    """The substrate is an installed package, so no checkout is located."""
    monkeypatch.delenv("PANTHER_REPO", raising=False)
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(_seal(tmp_path / "ws")))
    assert resolve_context().workspace == (tmp_path / "ws").resolve()


def test_a_missing_config_is_refused(monkeypatch):
    monkeypatch.delenv("AI_RFC_CONFIG", raising=False)
    with pytest.raises(EnvError):
        resolve_context()


def test_a_config_that_is_not_a_file_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_RFC_CONFIG", str(tmp_path / "absent" / "recon.yaml"))
    with pytest.raises(EnvError):
        resolve_context()


def test_a_declared_workspace_that_is_not_a_directory_is_refused(tmp_path, monkeypatch):
    """The unsealed branch trusts the field, so the field is checked."""
    config = tmp_path / "desk" / "recon.yaml"
    config.parent.mkdir()
    config.write_text(
        "name: demo\n"
        f"workspace: {tmp_path / 'absent'}\n"
        f"toolchain: {tmp_path / 'absent-toolchain.json'}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n"
    )
    monkeypatch.setenv("AI_RFC_CONFIG", str(config))
    with pytest.raises(EnvError):
        resolve_context()


def test_the_toolchain_handle_is_optional_but_must_be_a_file(monkeypatch, tmp_path):
    # Pinned for the reason tests/conftest.py:133-138 gives: unpinned, the
    # config's defaulted `toolchain:` finds the operator's own record and this
    # never reaches the environment handle at all.
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(_seal(tmp_path / "ws")))
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    assert resolve_context().toolchain is None
    record = tmp_path / "toolchain.json"
    record.write_text("{}")
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(record))
    assert resolve_context().toolchain == record
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(tmp_path / "missing.json"))
    with pytest.raises(EnvError):
        resolve_context()


def test_the_config_field_outranks_the_environment_handle(monkeypatch, tmp_path):
    """The handle is the fallback, so a config that names a real record wins."""
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    declared = tmp_path / "declared.json"
    declared.write_text("{}")
    fallback = tmp_path / "fallback.json"
    fallback.write_text("{}")
    config = _seal(tmp_path / "ws", declares=f"toolchain: {declared}\n")
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(config))
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(fallback))
    assert resolve_context().toolchain == declared


def test_a_copied_workspace_resolves_to_the_copy_not_the_original(
    tmp_path, monkeypatch
):
    """A campaign run is a copy of the pristine, and the sealed config names
    the pristine.

    `lifecycle/common.load_pair:129-132` records why the sealed `workspace:`
    field cannot be trusted. Deriving the workspace from it sends every tool to
    the tree the copy was made from — operating on the wrong workspace while
    looking like success, the failure `paths.py:4-6` exists to stop.
    """
    pristine = tmp_path / "pristine"
    (pristine / "out").mkdir(parents=True)
    (pristine / "init.json").write_text("{}")
    (pristine / "recon.yaml").write_text(
        "name: demo\n"
        f"workspace: {pristine}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n"
    )
    copy = tmp_path / "runs" / "A1" / "workspace"
    copy.parent.mkdir(parents=True)
    shutil.copytree(pristine, copy)

    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(copy / "recon.yaml"))
    assert resolve_context().workspace == copy.resolve()


def test_an_operator_config_outside_a_workspace_resolves_to_its_field(
    tmp_path, monkeypatch
):
    """Nothing sealed this config, so the workspace is the one it names.

    The sealed branch is gated on the file's own name; an operator keeps their
    ``recon.yaml`` where they like and the tree it points at is elsewhere.
    """
    declared = tmp_path / "elsewhere"
    declared.mkdir()
    config = tmp_path / "desk" / "recon.yaml"
    config.parent.mkdir()
    config.write_text(
        "name: demo\n"
        f"workspace: {declared}\n"
        f"toolchain: {tmp_path / 'absent-toolchain.json'}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n"
    )
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(config))
    assert resolve_context().workspace == declared.resolve()


def test_a_stray_init_record_beside_an_unsealed_config_does_not_seal_it(
    tmp_path, monkeypatch
):
    """Both halves of the seal are required, and this directory holds only one.

    ``init`` writes *both* the sealed ``recon.yaml`` and the ``init.json``. A
    directory that happens to hold an ``init.json`` beside a config under some
    other name was never initialised, and reading the workspace out of its
    parent would silently operate on the operator's desk.
    """
    declared = tmp_path / "elsewhere"
    declared.mkdir()
    desk = tmp_path / "desk"
    desk.mkdir()
    (desk / "init.json").write_text("{}")
    config = desk / "candidate.yaml"
    config.write_text(
        "name: demo\n"
        f"workspace: {declared}\n"
        f"toolchain: {tmp_path / 'absent-toolchain.json'}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n"
    )
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(config))
    assert resolve_context().workspace == declared.resolve()


def test_a_defaulted_toolchain_path_that_does_not_exist_resolves_to_None(
    tmp_path, monkeypatch
):
    """`config.py:642` defaults `toolchain` to a path, so a loaded config never carries
    None. Reading the field directly would make `core/draft.py:120`'s
    `if ctx.toolchain is not None:` always true and force a build on every tag."""
    # The default hangs off the experiments root, and an operator's own root
    # holds a real toolchain.json — the trap tests/conftest.py:133-138 names.
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    sealed = tmp_path / "ws"
    sealed.mkdir()
    (sealed / "init.json").write_text("{}")
    (sealed / "recon.yaml").write_text(
        "name: demo\n"
        f"workspace: {sealed}\n"
        "source:\n  repo: https://example.invalid/r.git\n  pin: deadbeef\n"
        "draft:\n  name: draft-demo\n"
    )
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(sealed / "recon.yaml"))
    assert resolve_context().toolchain is None
