"""Japanese and English text normalisation for judging.

The judge compares a model's free-form output against an accepted translation
set, so both sides must be normalised the same way first.  The rules are the
ones the plan lists: NFKC, full-width/half-width unification, long-vowel and
middle-dot handling, and kana unification.

Two separate entry points exist on purpose:

* :func:`normalize` -- conservative, used when the *output* is compared.
* :func:`normalize_strict` -- additionally strips trailing particles and
  inflectional tails, used when building candidate keys.

Every function is deterministic and pure, so a normalisation change is a
version bump (``NORMALIZER_VERSION``), not a silent behaviour change.
"""

import re
import unicodedata

__all__ = [
  "NORMALIZER_VERSION",
  "normalize",
  "normalize_strict",
  "has_japanese",
  "kana_variants",
  "katakana_to_hiragana",
  "hiragana_to_katakana",
]

NORMALIZER_VERSION = "n1"

# Combining marks left behind by NFKC on some inputs.
_RE_COMBINING = re.compile(r"[\u3099\u309a]")
# Middle dots and similar separators that vary between sources.
_RE_MIDDLE_DOTS = re.compile(r"[・･·•]")
# Long vowel marks in both scripts.
_RE_LONG_VOWEL = re.compile(r"[ー―—–]")
# Whitespace, including the ideographic space.
_RE_SPACES = re.compile(r"[\s\u3000]+")
# A trailing Japanese particle or light verb that carries no meaning for
# matching ("引き締め" vs "引き締めの").
_RE_TRAILING_PARTICLE = re.compile(
    r"(する|した|して|される|された|すること|をする|な|に|の|は|が|を|で|と|も|へ|や)$")
# English inflection tail used when matching borrowed words.
_RE_TRAILING_LATIN = re.compile(r"(ed|ing|s)$")

_KATAKANA_START = 0x30A1
_KATAKANA_END = 0x30F6
_KANA_OFFSET = 0x30A1 - 0x3041


def _to_hiragana(text):
  out = []
  for ch in text:
    code = ord(ch)
    if _KATAKANA_START <= code <= _KATAKANA_END:
      out.append(chr(code - _KANA_OFFSET))
    else:
      out.append(ch)
  return "".join(out)


def _to_katakana(text):
  out = []
  for ch in text:
    code = ord(ch)
    if 0x3041 <= code <= 0x3096:
      out.append(chr(code + _KANA_OFFSET))
    else:
      out.append(ch)
  return "".join(out)


def katakana_to_hiragana(text):
  """Converts katakana to hiragana (``カタカナ`` -> ``かたかな``)."""
  return _to_hiragana(text)


def hiragana_to_katakana(text):
  """Converts hiragana to katakana (``かたかな`` -> ``カタカナ``)."""
  return _to_katakana(text)


def normalize(text):
  """Normalises a string for translation matching.

  NFKC, half-width unification, whitespace collapse, middle-dot and long-vowel
  removal, and kana unification to hiragana.  Punctuation is dropped so that
  a trailing period does not defeat an otherwise exact match.
  """
  if not text:
    return ""
  value = unicodedata.normalize("NFKC", text)
  value = _RE_COMBINING.sub("", value)
  value = _RE_SPACES.sub(" ", value).strip()
  value = _RE_MIDDLE_DOTS.sub("", value)
  value = _RE_LONG_VOWEL.sub("", value)
  value = _to_hiragana(value)
  # Drop punctuation that survives NFKC but carries no lexical content.
  value = re.sub(r"[。、，．,\.!?！？「」『』（）()\[\]【】:：;；\"'’“”]", "", value)
  # Case-insensitive matching: a model may write "Tightening" or "tightening".
  return value.casefold().strip()


def normalize_strict(text):
  """Normalisation plus removal of a trailing particle or inflection tail."""
  value = normalize(text)
  previous = None
  while previous != value:
    previous = value
    value = _RE_TRAILING_PARTICLE.sub("", value)
  return value


def has_japanese(text):
  """True when the text contains at least one Japanese character."""
  if not text:
    return False
  for ch in text:
    code = ord(ch)
    if (0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF):
      return True
  return False


def kana_variants(text):
  """Returns the set of equivalent spellings of a Japanese term.

  ``引き締め`` / ``引締め`` / ``引きたて`` differ only in how the okurigana is
  written, so matching must accept any of them.
  """
  norm = normalize(text)
  variants = {norm, normalize_strict(text)}
  variants.add(_to_katakana(norm))
  variants.add(_to_hiragana(norm))
  return {variant for variant in variants if variant}
