"""The recognizer's beam search, over a scripted decoder: no model needed."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sympy_editor_handwriting.recognizer import _beam_search  # noqa: E402

BOS, EOS, X, CTX = 1, 2, 4, 5


class _Enc:
    def run(self, _names, feeds):
        n = len(feeds["src"])
        return [np.zeros((n, 3, 8), dtype=np.float32), np.zeros((n, 3), dtype=bool)]


class _Dec:
    """A decoder that likes the stand-in best, every time - so, left to itself,
    it writes it again and again - and the end next."""

    def run(self, _names, feeds):
        logits = np.full((len(feeds["tokens"]), 6), -8.0, dtype=np.float32)
        logits[:, CTX], logits[:, EOS], logits[:, X] = 0.0, -0.5, -1.0
        return [logits]


def _search(**kw):
    return _beam_search(_Enc(), _Dec(), np.zeros((4, 7)), BOS, EOS, beam=4, max_tokens=6, **kw)


def test_unconstrained_the_stand_in_repeats():
    assert any(ids.count(CTX) != 1 for _, ids in _search())


def test_with_a_box_every_reading_holds_the_stand_in_once():
    found = _search(once=[CTX])
    assert found and all(ids.count(CTX) == 1 for _, ids in found)


def test_without_a_box_no_reading_holds_it():
    found = _search(never=[CTX])
    assert found and all(CTX not in ids for _, ids in found)
