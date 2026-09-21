"""The Markdown primitives at the package root they were promoted to.

``ai_rfc/report.py`` is the production renderer and sits above no package, so
a span escaper living under ``ai_rfc/experiment/`` could only be reached by
the substrate importing the experiment — the edge this package's layering
forbids. The module moved rather than being copied, and this file is where
:func:`~ai_rfc.markdown.code` is checked against the fence rule it claims,
because the rule is CommonMark's rather than either renderer's.

The fence assertions state the *rule* — one backtick longer than the longest
run inside — rather than a rendering of one hand-picked value, so a renderer
that happened to satisfy the example and not the rule is caught.
"""

import pytest

from ai_rfc.markdown import cell, code, fmt, separator

pytestmark = pytest.mark.unit

#: The two line endings CommonMark names, built by :func:`chr` rather than
#: written: ``tests/substrate/test_source_hygiene.py`` forbids a source file
#: carrying a character :meth:`str.isprintable` refuses.
NEWLINE = chr(10)
CARRIAGE_RETURN = chr(13)

#: What ``fmt`` and ``code`` render for a value nothing measured.
EM_DASH = chr(0x2014)


def _fence(rendered: str) -> str:
    """The opening backtick run of a rendered span.

    Args:
        rendered: What :func:`~ai_rfc.markdown.code` returned.

    Returns:
        The leading run of backticks, which is the span's fence.
    """
    return rendered[: len(rendered) - len(rendered.lstrip("`"))]


def _inside(rendered: str) -> str:
    """A rendered span's content, its fence removed from both ends.

    Args:
        rendered: What :func:`~ai_rfc.markdown.code` returned.

    Returns:
        Everything between the two fences, padding spaces included.
    """
    fence = _fence(rendered)
    return rendered.removeprefix(fence).removesuffix(fence)


@pytest.mark.parametrize("run", ["", "`", "``", "```"])
def test_the_fence_is_one_longer_than_the_longest_run_inside(run):
    """CommonMark closes a span on the first run of the opening run's length.

    A value carrying a run as long as its fence closes the span early and
    everything after it goes live as markup, which is the defect this escaper
    exists for. Zero, one, two and three are the enumeration's first members;
    the implementation measures rather than enumerates.
    """
    rendered = code(f"a{run}b")
    fence = _fence(rendered)

    assert len(fence) == len(run) + 1
    assert rendered.endswith(fence)
    assert _inside(rendered) == f"a{run}b"


def test_a_leading_backtick_is_held_off_its_fence_by_a_padding_space():
    """Without the pad the value's backtick joins the fence and lengthens it."""
    rendered = code("`x")
    fence = _fence(rendered)

    assert rendered.startswith(f"{fence} ")
    assert rendered.endswith(f" {fence}")
    assert _inside(rendered) == " `x "


def test_a_trailing_backtick_is_held_off_its_fence_by_a_padding_space():
    rendered = code("x`")
    fence = _fence(rendered)

    assert rendered.startswith(f"{fence} ")
    assert _inside(rendered) == " x` "


def test_a_value_with_no_backtick_pays_for_no_padding():
    """The pad is the fence rule's cost, not its habit."""
    assert code("plain") == "`plain`"


def test_nothing_measured_renders_as_the_em_dash_and_not_as_a_span():
    """An empty span reads as a measured empty string; the dash does not."""
    assert code(None) == EM_DASH
    assert "`" not in code(None)


@pytest.mark.parametrize("breaker", [NEWLINE, CARRIAGE_RETURN])
def test_no_line_ending_inside_a_span_can_end_the_line(breaker):
    """CR alone ends a line under CommonMark exactly as LF does.

    Stated over both because neutralising ``\\n`` and calling it done is the
    enumeration the category predicate replaced: a span is one line, and the
    escape has to be visible in it rather than acting on it.
    """
    rendered = code("a" + breaker + "b")

    assert len(rendered.splitlines()) == 1
    assert breaker not in rendered
    assert rendered.endswith(_fence(rendered))


def test_the_table_primitives_answer_at_the_promoted_home():
    """``cell`` and ``separator`` moved with ``code`` and still agree.

    The separator counts only the pipes the cell left structural, so a pipe in
    a value must not widen it — the property the two were written together
    for, asserted here because the move is what could break it.
    """
    header = f"| {cell('a|b')} | {cell(None)} |"

    assert fmt(None) == EM_DASH
    assert cell("a|b") == "a\\|b"
    assert separator(header) == "|---|---|"
