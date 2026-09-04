"""Model-free substrate for reconstructed requirement manifests.

This package validates manifests produced by reconstruction agents running
outside the framework, weighs every claim against the evidence its anchors
point at, and reports the result. The substrate makes no model calls and opens
no socket except in ``forge``; ``server`` (the MCP door) and ``experiment``
(the harness that launches agents) are the two subpackages that are not
substrate, and each says so in its own README.
"""

__version__ = "0.1.0"

from .anchors import AnchorError, UnknownCommitError, verify
from .models import (
    Anchor,
    EvidenceClass,
    Intent,
    Manifest,
    RequirementClaim,
    RequirementClass,
    Status,
)
from .promotion import Violation, adjudicate, violations
from .report import ManifestReport, build, to_json, to_markdown, to_yaml
from .schema import SchemaError, dump, load

__all__ = [
    "Anchor",
    "AnchorError",
    "EvidenceClass",
    "Intent",
    "Manifest",
    "RequirementClass",
    "ManifestReport",
    "RequirementClaim",
    "SchemaError",
    "Status",
    "UnknownCommitError",
    "Violation",
    "adjudicate",
    "build",
    "dump",
    "load",
    "to_json",
    "to_markdown",
    "to_yaml",
    "verify",
    "violations",
]
