"""The ground-truth axis: claims checkable against pinned source.

The judge scores a draft by asking a model what it thinks. This axis does not
ask anything: each dataset entry names a file, a line and a value in a pinned
checkout, so a claim either matches the source or it does not.
"""

from importlib import resources
from typing import Any

import yaml

DATASET = "aioquic-w02-11.yaml"
PACKAGE = "ai_rfc.experiment.groundtruth"


def load_dataset(name: str = DATASET) -> dict[str, Any]:
    """Read a ground-truth dataset out of the installed package.

    The dataset is read through :mod:`importlib.resources` rather than a path
    relative to this file, so it resolves the same way from a wheel as from a
    source checkout.

    Args:
        name: File name of the dataset within the ``groundtruth`` package.

    Returns:
        The dataset document: ``repository`` and ``commit`` identify the
        checkout the anchors were read from, and ``entries`` holds the claims.
    """
    text = resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8")
    return yaml.safe_load(text)
