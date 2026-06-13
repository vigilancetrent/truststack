"""Phonetic encoding utilities.

Two pure-stdlib encoders ship by default so the package works offline with zero
extra dependencies:

* :func:`soundex` — American Soundex (4-char code).
* :func:`metaphone` — a faithful subset of Lawrence Philips' Metaphone, which
  captures more pronunciation nuance than Soundex (it is variable-length and
  handles digraphs such as ``PH``, ``SCH``, ``GH``).

When the optional ``phonetic`` extra (``jellyfish``) is installed, both
functions transparently delegate to it for a more battle-tested implementation.
The import is lazy so the package imports cleanly without it.
"""

from __future__ import annotations

# Soundex consonant-to-digit mapping (vowels, H, W, Y map to None / are dropped).
_SOUNDEX_CODES: dict[str, str] = {
    "B": "1",
    "F": "1",
    "P": "1",
    "V": "1",
    "C": "2",
    "G": "2",
    "J": "2",
    "K": "2",
    "Q": "2",
    "S": "2",
    "X": "2",
    "Z": "2",
    "D": "3",
    "T": "3",
    "L": "4",
    "M": "5",
    "N": "5",
    "R": "6",
}

_VOWELS = frozenset("AEIOU")


def _stdlib_soundex(name: str) -> str:
    """Compute the American Soundex code for ``name`` using only the stdlib.

    Returns a 4-character code like ``"J350"`` for an alphabetic input, or an
    empty string when the input contains no letters.
    """
    letters = [ch for ch in name.upper() if ch.isalpha()]
    if not letters:
        return ""

    first = letters[0]
    encoded = [first]
    # Track the digit of the previous letter to collapse adjacent duplicates.
    prev_code = _SOUNDEX_CODES.get(first)

    for ch in letters[1:]:
        code = _SOUNDEX_CODES.get(ch)
        if code is None:
            # Vowels / H,W,Y act as separators except H and W, which do not
            # reset adjacency. We treat all non-coded letters as separators for
            # simplicity, matching the common Soundex variant.
            if ch not in ("H", "W"):
                prev_code = None
            continue
        if code != prev_code:
            encoded.append(code)
            if len(encoded) == 4:
                break
        prev_code = code

    return "".join(encoded).ljust(4, "0")[:4]


def soundex(name: str) -> str:
    """Return the Soundex code for ``name``.

    Uses :mod:`jellyfish` when the optional ``phonetic`` extra is installed,
    otherwise falls back to the bundled stdlib implementation. The lazy import
    keeps the dependency optional.
    """
    try:
        import jellyfish  # lazy: only when the extra is installed
    except ImportError:
        return _stdlib_soundex(name)

    cleaned = "".join(ch for ch in name if ch.isalpha())
    if not cleaned:
        return ""
    return str(jellyfish.soundex(cleaned))


def _only_letters(name: str) -> str:
    """Uppercase, keep only ASCII letters (after NFKD folding of accents)."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in folded.upper() if "A" <= ch <= "Z")


def _stdlib_metaphone(name: str) -> str:
    """Compute a Metaphone code for ``name`` using only the stdlib.

    This is a pragmatic implementation of Lawrence Philips' Metaphone covering
    the common digraph rules (``PH``→``F``, ``SCH``→``SK``, ``TH``→``0``,
    silent ``GH``/``KN``/``GN``/``WR``, soft ``C``/``G``, etc.). It is
    deterministic and variable-length, returning ``""`` for input with no
    letters.
    """
    word = _only_letters(name)
    if not word:
        return ""

    length = len(word)

    def at(i: int) -> str:
        return word[i] if 0 <= i < length else ""

    def is_vowel(i: int) -> bool:
        return at(i) in _VOWELS

    out: list[str] = []
    i = 0

    # Initial-letter exceptions: drop the first letter of these digraphs.
    if word[:2] in ("AE", "GN", "KN", "PN", "WR"):
        i = 1
    elif word[:1] == "X":
        out.append("S")
        i = 1
    elif word[:2] == "WH":
        out.append("W")
        i = 2

    while i < length:
        ch = word[i]
        prev = at(i - 1)
        nxt = at(i + 1)
        nxt2 = at(i + 2)

        # Collapse doubled letters (except C, which has its own handling).
        if ch == prev and ch != "C":
            i += 1
            continue

        if ch in _VOWELS:
            # Keep vowels only at the start of the word.
            if i == 0:
                out.append(ch)
            i += 1
            continue

        if ch == "B":
            # Silent terminal B after M (e.g. DUMB).
            if not (i == length - 1 and prev == "M"):
                out.append("B")
            i += 1
        elif ch == "C":
            if nxt == "I" and nxt2 == "A":
                out.append("X")
            elif nxt == "H":
                out.append("S" if prev == "S" else "X")
                i += 1
            elif nxt in ("I", "E", "Y"):
                if prev != "S":  # SCI/SCE/SCY -> already an S sound
                    out.append("S")
            else:
                out.append("K")
            i += 1
        elif ch == "D":
            if nxt == "G" and nxt2 in ("E", "I", "Y"):
                out.append("J")
                i += 2
            else:
                out.append("T")
                i += 1
        elif ch == "F":
            out.append("F")
            i += 1
        elif ch == "G":
            if nxt == "H":
                if not (i > 0 and is_vowel(i - 1)) or is_vowel(i + 2):
                    out.append("K")
                i += 1
            elif nxt == "N":
                # Silent G in GN / GNED at end of word.
                pass
            elif nxt in ("I", "E", "Y"):
                out.append("J")
            else:
                out.append("K")
            i += 1
        elif ch == "H":
            # Voiced only between vowels and not after C/S/P/T/G (digraphs).
            if (is_vowel(i - 1) and not is_vowel(i + 1)) or prev in ("C", "S", "P", "T", "G"):
                pass
            else:
                out.append("H")
            i += 1
        elif ch == "J":
            out.append("J")
            i += 1
        elif ch in ("K",):
            if prev != "C":
                out.append("K")
            i += 1
        elif ch == "L":
            out.append("L")
            i += 1
        elif ch == "M":
            out.append("M")
            i += 1
        elif ch == "N":
            out.append("N")
            i += 1
        elif ch == "P":
            if nxt == "H":
                out.append("F")
                i += 1
            else:
                out.append("P")
            i += 1
        elif ch == "Q":
            out.append("K")
            i += 1
        elif ch == "R":
            out.append("R")
            i += 1
        elif ch == "S":
            if nxt == "H":
                out.append("X")
                i += 1
            elif nxt == "I" and nxt2 in ("O", "A"):
                out.append("X")
            else:
                out.append("S")
            i += 1
        elif ch == "T":
            if nxt == "H":
                out.append("0")
                i += 1
            elif nxt == "I" and nxt2 in ("O", "A"):
                out.append("X")
            else:
                out.append("T")
            i += 1
        elif ch == "V":
            out.append("F")
            i += 1
        elif ch == "W":
            if is_vowel(i + 1):
                out.append("W")
            i += 1
        elif ch == "X":
            out.append("K")
            out.append("S")
            i += 1
        elif ch == "Y":
            if is_vowel(i + 1):
                out.append("Y")
            i += 1
        elif ch == "Z":
            out.append("S")
            i += 1
        else:  # pragma: no cover - defensive; _only_letters guarantees A-Z
            i += 1

    return "".join(out)


def metaphone(name: str) -> str:
    """Return the Metaphone code for ``name``.

    Uses :mod:`jellyfish` when the optional ``phonetic`` extra is installed,
    otherwise falls back to the bundled stdlib implementation.
    """
    try:
        import jellyfish  # lazy: only when the extra is installed
    except ImportError:
        return _stdlib_metaphone(name)

    cleaned = _only_letters(name)
    if not cleaned:
        return ""
    return str(jellyfish.metaphone(cleaned))


def phonetic_equal(left: str, right: str) -> bool:
    """Return ``True`` when two strings share a non-empty Soundex code."""
    left_code = soundex(left)
    right_code = soundex(right)
    return bool(left_code) and left_code == right_code


def metaphone_equal(left: str, right: str) -> bool:
    """Return ``True`` when two strings share a non-empty Metaphone code."""
    left_code = metaphone(left)
    right_code = metaphone(right)
    return bool(left_code) and left_code == right_code


def phonetic_agreement(left: str, right: str) -> float:
    """Return a phonetic-agreement score in ``[0.0, 1.0]``.

    * ``1.0`` when both Soundex and Metaphone codes agree (strong signal).
    * ``0.5`` when exactly one of the two encoders agrees (weaker signal).
    * ``0.0`` when neither agrees (or either input has no letters).
    """
    score = 0.0
    if phonetic_equal(left, right):
        score += 0.5
    if metaphone_equal(left, right):
        score += 0.5
    return score


__all__ = [
    "metaphone",
    "metaphone_equal",
    "phonetic_agreement",
    "phonetic_equal",
    "soundex",
]
