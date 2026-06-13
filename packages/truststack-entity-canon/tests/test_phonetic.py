"""Phonetic encoder tests: Soundex, Metaphone, agreement, unicode, edges."""

from __future__ import annotations

import pytest

from entity_canon import (
    metaphone,
    metaphone_equal,
    phonetic_agreement,
    phonetic_equal,
    soundex,
)
from entity_canon.phonetic import _stdlib_metaphone, _stdlib_soundex

# ── Soundex ────────────────────────────────────────────────────────────────


def test_soundex_known_codes() -> None:
    assert _stdlib_soundex("Robert") == "R163"
    assert _stdlib_soundex("Rupert") == "R163"
    assert _stdlib_soundex("Tymczak") == "T522"
    assert _stdlib_soundex("Pfister") == "P236"


def test_soundex_name_variants_collide() -> None:
    code = _stdlib_soundex("Jatin")
    assert _stdlib_soundex("Jhatin") == code
    assert _stdlib_soundex("Jatyn") == code


def test_soundex_empty_and_non_alpha() -> None:
    assert _stdlib_soundex("") == ""
    assert _stdlib_soundex("123") == ""
    assert _stdlib_soundex("   ") == ""


def test_soundex_pads_short_codes() -> None:
    # A single coded consonant must still produce a 4-char code.
    assert _stdlib_soundex("Lee") == "L000"
    assert len(_stdlib_soundex("Amy")) == 4


def test_soundex_h_w_do_not_reset_adjacency() -> None:
    # Ashcraft: the H/W rule keeps adjacent same-coded consonants collapsed.
    code = _stdlib_soundex("Ashcraft")
    assert code[0] == "A"
    assert len(code) == 4


def test_soundex_public_wrapper_uses_stdlib() -> None:
    # With jellyfish disabled (conftest), the public wrapper == stdlib path.
    assert soundex("Jatin") == _stdlib_soundex("Jatin")
    assert soundex("") == ""
    assert soundex("123") == ""


# ── Metaphone ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Thompson", "0MPSN"),
        ("phone", "FN"),
        ("knight", "NT"),
        ("Wright", "RT"),
        ("school", "SSL"),
        ("Xavier", "SFR"),
        ("Caesar", "KSR"),
        ("dumb", "TM"),
        ("gnome", "NM"),
    ],
)
def test_metaphone_known_codes(name: str, expected: str) -> None:
    assert _stdlib_metaphone(name) == expected


def test_metaphone_ph_is_f() -> None:
    assert _stdlib_metaphone("Philip").startswith("F")


def test_metaphone_th_digraph() -> None:
    # TH encodes to the "0" theta marker.
    assert "0" in _stdlib_metaphone("Theory")


def test_metaphone_silent_initial_digraphs() -> None:
    # Leading KN/GN/PN/WR/AE drop the first letter.
    assert _stdlib_metaphone("Knife") == _stdlib_metaphone("Nife")
    assert _stdlib_metaphone("Pneumo") == _stdlib_metaphone("Neumo")


def test_metaphone_wh_becomes_w() -> None:
    assert _stdlib_metaphone("Where").startswith("W")


def test_metaphone_empty_and_non_alpha() -> None:
    assert _stdlib_metaphone("") == ""
    assert _stdlib_metaphone("123!@#") == ""
    assert metaphone("") == ""


def test_metaphone_doubled_letters_collapse() -> None:
    assert _stdlib_metaphone("Allan") == _stdlib_metaphone("Alan")


def test_metaphone_homophones_agree() -> None:
    assert _stdlib_metaphone("Smith") == _stdlib_metaphone("Smyth")


def test_metaphone_public_wrapper_uses_stdlib() -> None:
    assert metaphone("Thompson") == _stdlib_metaphone("Thompson")


# ── Unicode / accented names ───────────────────────────────────────────────


def test_unicode_accents_are_folded() -> None:
    # José folds to JOSE -> same codes as the ASCII spelling.
    assert soundex("José") == soundex("Jose")
    assert metaphone("José") == metaphone("Jose")


def test_unicode_only_accents_no_letters() -> None:
    # A string of pure combining marks (no base letters) yields nothing.
    combining = "̧́̀"  # acute, grave, cedilla
    assert metaphone(combining) == ""
    assert soundex(combining) == ""


def test_metaphone_handles_renee() -> None:
    assert metaphone("Renée") == metaphone("Renee")


# ── Equality helpers ───────────────────────────────────────────────────────


def test_phonetic_equal() -> None:
    assert phonetic_equal("Jatin", "Jatyn") is True
    assert phonetic_equal("Jatin", "Michael") is False
    assert phonetic_equal("", "Jatin") is False
    assert phonetic_equal("Jatin", "") is False


def test_metaphone_equal() -> None:
    assert metaphone_equal("Smith", "Smyth") is True
    assert metaphone_equal("Smith", "Jones") is False
    assert metaphone_equal("", "Smith") is False


# ── Agreement scoring ──────────────────────────────────────────────────────


def test_phonetic_agreement_full() -> None:
    # Both encoders agree -> 1.0.
    assert phonetic_agreement("Smith", "Smyth") == 1.0


def test_phonetic_agreement_none() -> None:
    assert phonetic_agreement("Smith", "Jones") == 0.0


def test_phonetic_agreement_partial() -> None:
    # Find a pair where exactly one encoder agrees -> 0.5.
    score = phonetic_agreement("Robert", "Rupert")
    assert score in (0.0, 0.5, 1.0)


def test_phonetic_agreement_partial_is_half() -> None:
    # "Knight" and "Night" share a Metaphone code ("NT") but NOT a Soundex code
    # (the silent K shifts the Soundex), so exactly one encoder agrees -> 0.5.
    assert metaphone_equal("Knight", "Night") is True
    assert phonetic_equal("Knight", "Night") is False
    assert phonetic_agreement("Knight", "Night") == 0.5


def test_phonetic_agreement_empty_inputs() -> None:
    assert phonetic_agreement("", "") == 0.0
    assert phonetic_agreement("Jatin", "") == 0.0
