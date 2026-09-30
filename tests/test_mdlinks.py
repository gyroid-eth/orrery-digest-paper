from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
from mdlinks import image_links  # noqa: E402


@pytest.mark.parametrize("line, expected", [
    ("![](file:///x/fig%20(poly(NIPAm)).png)", [("markdown", "file:///x/fig%20(poly(NIPAm)).png")]),
    # A raw space ends a destination (the plugin writes %20), so the second
    # link is incomplete and is reported, not dropped.
    ("![a](one.png) text ![b](two (1).png)", [("markdown", "one.png"), ("broken", "![b](two (1).png)")]),
    ("![a](<with space (1).png>)", [("markdown", "with space (1).png")]),
    ('![a](fig.png "Fig. 1 (a)")', [("markdown", "fig.png")]),
    ("![a](a\\)b.png)", [("markdown", "a)b.png")]),
    ("![alt [x]](fig.png)", [("markdown", "fig.png")]),
    ("![[v/p (1).png|500]]", [("wiki", "v/p (1).png|500")]),
])
def test_links_are_scanned_with_nesting_escapes_and_titles(line, expected):
    assert [(k, t) for k, t, _ in image_links(line)] == expected


@pytest.mark.parametrize("line", [
    "![](file:///x/fig%20(poly(NIPAm).png)",   # unbalanced
    "![alt](",
    "![[never closed",
    "![alt] (space before paren)",
])
def test_an_incomplete_image_link_is_reported_not_dropped(line):
    kinds = [k for k, _t, _s in image_links(line)]
    assert "broken" in kinds


def test_plain_text_has_no_links():
    assert image_links("a (b) c [d](e) f") == []
