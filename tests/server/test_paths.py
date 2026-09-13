"""The environment contract: a required config, and an optional toolchain."""

import shutil

import pytest
import yaml

from ai_rfc.server.paths import EnvError, resolve_context


def _config(root, **extra):
    """The smallest ``recon.yaml`` body that validates, as a dict.

    Args:
        root: The directory the ``workspace:`` field names.
        **extra: Further top-level keys, such as ``toolchain``.

    Returns:
        The document, ready for :func:`yaml.safe_dump`.
    """
    document = {
        "name": "demo",
        "workspace": str(root),
        "source": {"repo": "https://example.invalid/r.git", "pin": "deadbeef"},
        "draft": {"name": "draft-demo"},
    }
    document.update({key: str(value) for key, value in extra.items()})
    return document


def _write(config, root, **extra):
    """Emit a config rather than format one.

    ``tmp_path`` is ``TMPDIR``-derived, so a root carrying a ``:``, a ``#`` or
    a quote is the emitter's problem to quote — the same reason
    ``server/testing.seal`` dumps instead of interpolating, and these fixtures
    exist to prove that fix rather than to repeat the defect.

    Args:
        config: The file to write.
        root: The directory the ``workspace:`` field names.
        **extra: Further top-level keys.

    Returns:
        The path written.
    """
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump(_config(root, **extra), sort_keys=True))
    return config


def _seal(root, **extra):
    """Write the two files ``init`` writes, and return the sealed config's path.

    Args:
        root: Directory to seal; created if absent.
        **extra: Further top-level config keys, such as ``toolchain``.

    Returns:
        The path of the sealed ``recon.yaml``.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "init.json").write_text("{}")
    return _write(root / "recon.yaml", root, **extra)


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
    config = _write(
        tmp_path / "desk" / "recon.yaml",
        tmp_path / "absent",
        toolchain=tmp_path / "absent-toolchain.json",
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
    config = _seal(tmp_path / "ws", toolchain=declared)
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
    _seal(pristine)
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

    This is also what stops the unsealed-workspace refusal below from
    swallowing the ordinary case: a desk holds a ``recon.yaml`` and nothing a
    stage writes, so there is no workspace here to be operating on by mistake
    and the field is trusted.
    """
    declared = tmp_path / "elsewhere"
    declared.mkdir()
    config = _write(
        tmp_path / "desk" / "recon.yaml",
        declared,
        toolchain=tmp_path / "absent-toolchain.json",
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
    config = _write(
        desk / "candidate.yaml",
        declared,
        toolchain=tmp_path / "absent-toolchain.json",
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
    sealed = _seal(tmp_path / "ws")
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.delenv("AI_RFC_TOOLCHAIN", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(sealed))
    assert resolve_context().toolchain is None


def test_a_run_workspace_that_lost_its_seal_is_refused_not_redirected(
    tmp_path, monkeypatch
):
    """The seal's absence must stop the resolver, not steer it.

    A run's workspace is a copy of the pristine and its sealed ``workspace:``
    field names the pristine. Gating only on ``init.json`` meant that losing
    that one file mid-run resolved every tool to the pristine — the shared
    baseline every other run is copied from, so a write there contaminates a
    whole campaign, and nothing would have said so.
    """
    pristine = tmp_path / "pristine"
    (pristine / "out").mkdir(parents=True)
    _seal(pristine)
    copy = tmp_path / "runs" / "A1" / "workspace"
    copy.parent.mkdir(parents=True)
    shutil.copytree(pristine, copy)
    (copy / "init.json").unlink()

    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(copy / "recon.yaml"))
    with pytest.raises(EnvError) as refusal:
        resolve_context()
    # The two trees are byte-identical bar the seal, so a refusal that did not
    # name the missing file would leave an operator unable to tell them apart.
    assert "init.json" in str(refusal.value)


def test_a_toolchain_handle_naming_no_file_is_refused_however_the_config_reads(
    monkeypatch, tmp_path
):
    """Outranked is not unchecked.

    The config's ``toolchain:`` wins when it names a file, but a handle
    pointing at nothing is an operator's typo either way, and a precedence
    rule that swallowed it would hide the typo exactly when the config
    happened to be well provisioned.
    """
    monkeypatch.setenv("AI_RFC_EXPERIMENTS_ROOT", str(tmp_path / "root"))
    declared = tmp_path / "declared.json"
    declared.write_text("{}")
    monkeypatch.delenv("AI_RFC_WORKSPACE", raising=False)
    monkeypatch.setenv("AI_RFC_CONFIG", str(_seal(tmp_path / "ws", toolchain=declared)))
    monkeypatch.setenv("AI_RFC_TOOLCHAIN", str(tmp_path / "typo.json"))
    with pytest.raises(EnvError) as refusal:
        resolve_context()
    assert "AI_RFC_TOOLCHAIN" in str(refusal.value)


def test_a_refused_workspace_names_the_handle_it_came_from(tmp_path, monkeypatch):
    """Two things can be wrong, and the message has to say which to edit.

    The workspace is derived now, so a bare "not a directory" leaves an
    operator guessing between ``AI_RFC_CONFIG`` and the ``workspace:`` field
    inside the file it names.
    """
    config = _write(
        tmp_path / "desk" / "recon.yaml",
        tmp_path / "absent",
        toolchain=tmp_path / "absent-toolchain.json",
    )
    monkeypatch.setenv("AI_RFC_CONFIG", str(config))
    with pytest.raises(EnvError) as refusal:
        resolve_context()
    assert "AI_RFC_CONFIG" in str(refusal.value) and str(config) in str(refusal.value)
