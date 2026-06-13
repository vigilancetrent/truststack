"""Branch coverage for the stdlib Metaphone rules and the jellyfish fallbacks."""

from __future__ import annotations

import sys
import types

import pytest

from entity_canon.phonetic import metaphone, soundex


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("phone", "FN"),  # PH -> F
        ("aesop", "SP"),  # AE initial -> drop A
        ("llama", "LM"),  # doubled letter collapse
        ("city", "ST"),  # soft C (C+I, prev != S) -> S
        ("kite", "KT"),  # K (prev != C) -> K
        ("quiz", "KS"),  # Q -> K
        ("fox", "FKS"),  # X mid -> K,S
        ("yes", "YS"),  # Y + vowel -> Y
        ("wine", "WN"),  # W + vowel -> W
        ("zoo", "S"),  # Z -> S
        ("wheel", "WL"),  # WH initial -> W
    ],
)
def test_metaphone_exact_codes(word: str, expected: str) -> None:
    assert metaphone(word) == expected


def test_metaphone_x_producing_digraphs() -> None:
    assert "X" in metaphone("fascia")  # C + IA -> X
    assert "X" in metaphone("chef")  # CH -> X
    assert "X" in metaphone("nation")  # T + IO -> X
    assert "X" in metaphone("tension")  # S + IO -> X
    assert "X" in metaphone("shoe")  # SH -> X


def test_metaphone_g_and_gh_rules() -> None:
    assert metaphone("ghost").startswith("K")  # GH at start -> K
    assert metaphone("knight") == metaphone("night")  # KN initial + silent GH
    assert "J" in metaphone("gem")  # soft G -> J
    assert "K" in metaphone("go")  # hard G -> K
    assert "J" in metaphone("judge")  # DG + E -> J


def test_metaphone_v_and_voiced_h() -> None:
    assert metaphone("vine").startswith("F")  # V -> F
    assert "H" in metaphone("ahead")  # H voiced between vowels


def test_metaphone_initial_silent_digraphs() -> None:
    # PN/WR/GN initials drop their first letter.
    assert metaphone("pneumonia").startswith("N")
    assert metaphone("wrack").startswith("R")
    assert metaphone("xavier").startswith("S")  # leading X -> S


def test_metaphone_no_letters_is_empty() -> None:
    assert metaphone("12345") == ""
    assert metaphone("") == ""


def _install_fake_jellyfish(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("jellyfish")
    fake.soundex = lambda s: f"S-{s.upper()}"  # type: ignore[attr-defined]
    fake.metaphone = lambda s: f"M-{s.upper()}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jellyfish", fake)


def test_soundex_uses_jellyfish_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_jellyfish(monkeypatch)
    assert soundex("smith") == "S-SMITH"
    assert soundex("123") == ""  # no letters -> empty before delegating


def test_metaphone_uses_jellyfish_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_jellyfish(monkeypatch)
    assert metaphone("smith") == "M-SMITH"
    assert metaphone("123") == ""  # no letters -> empty before delegating
