"""Ground-truth datasets, shipped as package data.

The directory holds YAML only, and this module makes it a declared package for
``[tool.setuptools.packages.find]``, which is the plain finder rather than
``find_namespace``.

What is measured, on setuptools 65.5.0 and CPython 3.10.12: the
``"ai_rfc.experiment.groundtruth" = ["*.yaml"]`` entry in ``package-data`` is
what puts the dataset in the wheel — build without it from a clean tree and
only this module ships. Dropping this file does *not* by itself lose the YAML
on that toolchain, so the entry, not the ``__init__.py``, is the part a
packaging change must not delete.

Two build artifacts make that easy to mismeasure, and both did here: a stale
``build/lib`` and a stale ``ai_rfc.egg-info/SOURCES.txt`` each keep shipping a
data file whose ``package-data`` entry has been removed. Remove both before
trusting a wheel built to answer this question.
"""
