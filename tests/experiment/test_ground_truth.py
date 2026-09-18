"""The aioquic ground-truth dataset and the anchors every entry claims.

This axis consults no model. Each entry names a file, a line and a value in a
pinned checkout, so the two anchor tests can resolve that triple against the
source itself: a dataset that passes them is ground truth in a way a judge's
opinion is not. The anchors skip rather than fail when the checkout is absent
or sits at a different commit, because the pinned tree is evidence kept
outside the repository.
"""

import re
from pathlib import Path

import pytest

from ai_rfc.experiment.ground_truth import load_dataset

pytestmark = pytest.mark.unit

CHECKOUT = Path.home() / "arfc-experiments" / "pristine" / "aioquic-w02-11" / "clone"

KINDS = {"constant", "error_code", "frame_type", "setting", "stream_type"}


def _checkout_commit(root: Path) -> str | None:
    """Resolve a checkout's HEAD commit without shelling out to git.

    Args:
        root: The working tree whose ``.git`` directory is read.

    Returns:
        The 40-character commit hash, or ``None`` when no ref resolves.
    """
    head = root / ".git" / "HEAD"
    if not head.is_file():
        return None
    contents = head.read_text().strip()
    if not contents.startswith("ref: "):
        return contents
    ref = contents[len("ref: ") :]
    loose = root / ".git" / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = root / ".git" / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            if line.endswith(f" {ref}"):
                return line.split(" ", 1)[0]
    return None


@pytest.fixture
def pinned_checkout() -> Path:
    """The pristine aioquic tree the dataset anchors into, or a skip."""
    dataset = load_dataset()
    if not CHECKOUT.is_dir():
        pytest.skip(f"the pinned aioquic checkout is absent at {CHECKOUT}")
    found = _checkout_commit(CHECKOUT)
    pinned = dataset["commit"]
    if found != pinned:
        pytest.skip(f"{CHECKOUT} is at {found}, the dataset pins {pinned}")
    return CHECKOUT


def test_every_entry_has_a_unique_id_and_the_required_fields():
    entries = load_dataset()["entries"]
    assert entries, "the dataset is empty"
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))
    for entry in entries:
        assert {
            "id",
            "statement",
            "symbol",
            "value",
            "source",
            "rfc",
            "kind",
        } <= entry.keys()
        assert {"path", "line"} <= entry["source"].keys()
        assert {"doc", "section"} <= entry["rfc"].keys()


def test_the_dataset_is_reachable_as_a_package_resource():
    """The YAML resolves through the package, not through a filesystem path.

    This runs against the source tree, where ``is_file()`` holds whenever the
    YAML sits beside ``__init__.py``, so it would stay green with the
    ``package-data`` entry deleted and says nothing about the wheel. The
    evidence for the wheel is the build measured in
    ``ai_rfc/experiment/groundtruth/__init__.py``: on setuptools 65.5.0 and
    CPython 3.10.12 the ``package-data`` entry is the load-bearing half, and
    dropping ``__init__.py`` does not by itself lose the YAML.
    """
    import importlib.resources as res

    assert (
        res.files("ai_rfc.experiment.groundtruth")
        .joinpath("aioquic-w02-11.yaml")
        .is_file()
    )


def test_every_value_and_symbol_is_text():
    """YAML reads an unquoted ``0x100`` as the integer 256.

    Coerced that way, the anchor test's substring check would compare an int
    against a source line and never discriminate anything, so the literal has
    to survive loading as the text the source spells.
    """
    for entry in load_dataset()["entries"]:
        assert isinstance(entry["value"], str), entry["id"]
        assert isinstance(entry["symbol"], str), entry["id"]


def test_every_kind_comes_from_the_closed_vocabulary():
    for entry in load_dataset()["entries"]:
        assert entry["kind"] in KINDS, entry["id"]


def test_every_source_path_is_repo_relative():
    """The dataset travels in the wheel; an absolute path would not survive."""
    for entry in load_dataset()["entries"]:
        path = entry["source"]["path"]
        assert not path.startswith("/"), entry["id"]
        assert ".." not in Path(path).parts, entry["id"]
        assert isinstance(entry["source"]["line"], int), entry["id"]
        assert entry["source"]["line"] >= 1, entry["id"]


def test_every_rfc_citation_names_a_document_and_a_section():
    for entry in load_dataset()["entries"]:
        assert re.fullmatch(r"RFC \d+", entry["rfc"]["doc"]), entry["id"]
        assert re.fullmatch(r"\d+(\.\d+)*", str(entry["rfc"]["section"])), entry["id"]


def test_the_dataset_pins_the_commit_its_anchors_were_read_from():
    dataset = load_dataset()
    assert re.fullmatch(r"[0-9a-f]{40}", dataset["commit"])
    assert dataset["repository"]


def test_every_source_resolves_to_a_real_line(pinned_checkout):
    """Anchor 1: the cited ``file:line`` exists in the pinned checkout."""
    for entry in load_dataset()["entries"]:
        path = pinned_checkout / entry["source"]["path"]
        assert path.is_file(), f"{entry['id']}: {path} is not a file"
        lines = path.read_text().splitlines()
        line = entry["source"]["line"]
        assert 1 <= line <= len(lines), (
            f"{entry['id']}: {entry['source']['path']} has {len(lines)} lines, "
            f"the entry cites line {line}"
        )


def test_every_value_appears_at_the_line_it_cites(pinned_checkout):
    """Anchor 2: the cited line really spells the symbol and the value."""
    for entry in load_dataset()["entries"]:
        path = pinned_checkout / entry["source"]["path"]
        text = path.read_text().splitlines()[entry["source"]["line"] - 1]
        location = f"{entry['source']['path']}:{entry['source']['line']}"
        assert entry["symbol"] in text, (
            f"{entry['id']}: {location} is {text!r}, "
            f"which does not spell {entry['symbol']!r}"
        )
        assert entry["value"] in text, (
            f"{entry['id']}: {location} is {text!r}, "
            f"which does not spell {entry['value']!r}"
        )
