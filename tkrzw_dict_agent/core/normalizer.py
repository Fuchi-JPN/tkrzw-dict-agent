"""Text normalization and word extraction utilities.

Thin, well-typed wrappers over the normalization helpers that ``tkrzw-dict``
already provides, so the agent layer never touches upstream modules directly.
"""

import regex

from . import upstream

__all__ = [
    "normalize_word",
    "predict_language",
    "english_ratio",
    "is_english_heavy",
    "extract_words",
    "split_sentences",
    "is_stop_word",
    "is_numeric_word",
    "remove_diacritic",
]

# Matches a Latin script word, allowing internal apostrophes and hyphens,
# mirroring UnionSearcher.re_latin_word.
_RE_LATIN_WORD = regex.compile(r"[\p{Latin}\d][-_'’\p{Latin}\d]*")

# Sentence terminator for the supported languages.
_RE_SENTENCE_END = regex.compile(r"[。.!?！？\n]+")

# English ratio above which the sidecar considers a text worth enriching.
ENGLISH_RATIO_THRESHOLD = 0.30

# Function words that never carry meaning worth a vocabulary hint.  The
# vendored engine only filters pronouns and a few auxiliaries; the sidecar
# additionally drops prepositions, conjunctions and the rest of the closed
# class, per the "blacklist unnecessary words" policy of the specification.
_ENGLISH_STOP_WORDS = frozenset("""
a about above after again against all am an and any are aren't as at be
because been before being below between both but by can can't cannot could
couldn't did didn't do does doesn't doing don't down during each few for from
further had hadn't has hasn't have haven't having he he'd he'll he's her here
here's hers herself him himself his how how's i i'd i'll i'm i've if in into
is isn't it it's its itself let's me more most mustn't my myself no nor not
of off on once only or other ought our ours ourselves out over own same shan't
she she'd she'll she's should shouldn't so some such than that that's the
their theirs them themselves then there there's these they they'd they'll
they're they've this those through to too under until up very was wasn't we
we'd we'll we're we've were weren't what what's when when's where where's which
while who who's whom why why's with won't would wouldn't you you'd you'll
you're you've your yours yourself yourselves also just now then thus however
therefore moreover furthermore meanwhile nevertheless nonetheless
""".split())


def normalize_word(text):
  """Normalizes a word for dictionary lookup.

  Delegates to the upstream normalizer so lookup keys are always identical to
  the ones the search engine produces: whitespace collapsed, middle dots and
  diacritics stripped, lower-cased.
  """
  return upstream.tkrzw_dict.NormalizeWord(text)


def remove_diacritic(text):
  """Strips diacritical marks from Latin characters (upstream semantics)."""
  return upstream.tkrzw_dict.RemoveDiacritic(text)


def predict_language(text):
  """Predicts the dominant language of the text: "en", "ja", or "other"."""
  if not text:
    return "other"
  latin = len(regex.findall(r"\p{Latin}", text))
  kana = len(regex.findall(r"[぀-ヿ]", text))
  han = len(regex.findall(r"\p{Han}", text))
  if kana + han > latin:
    return "ja"
  if latin > 0:
    return "en"
  return "other"


def english_ratio(text):
  """Returns the ratio of Latin script characters in the text."""
  if not text:
    return 0.0
  letters = regex.findall(r"\p{L}|\p{N}", text)
  if not letters:
    return 0.0
  latin = sum(1 for ch in letters if regex.match(r"\p{Latin}|\p{N}", ch))
  return latin / len(letters)


def is_english_heavy(text):
  """True when the text is English-dominant enough to enrich."""
  return predict_language(text) == "en" or english_ratio(text) > ENGLISH_RATIO_THRESHOLD


def extract_words(text):
  """Extracts the Latin word tokens of the text in order.

  Returns a list of (surface, start_offset) pairs so callers can map a
  candidate back to its position in the source text.
  """
  result = []
  for match in _RE_LATIN_WORD.finditer(text):
    surface = match.group()
    if regex.search(r"\p{Latin}", surface):
      result.append((surface, match.start()))
  return result


def split_sentences(text):
  """Splits the text into sentences, keeping terminators attached."""
  if not text:
    return []
  sentences = []
  cursor = 0
  for match in _RE_SENTENCE_END.finditer(text):
    end = match.end()
    chunk = text[cursor:end].strip()
    if chunk:
      sentences.append(chunk)
    cursor = end
  tail = text[cursor:].strip()
  if tail:
    sentences.append(tail)
  return sentences


def is_stop_word(language, word):
  """True when the word is a function word not worth looking up.

  Applies the sidecar's expanded English function-word list first, then falls
  back to the vendored engine's rules (digits, Japanese function words).
  """
  if not word:
    return True
  normalized = word.lower() if isinstance(word, str) else word
  if language == "en" and normalized in _ENGLISH_STOP_WORDS:
    return True
  return bool(upstream.tkrzw_dict.IsStopWord(language, word))


def is_numeric_word(word):
  """True when the word is (mostly) numeric."""
  if not word:
    return False
  return bool(upstream.tkrzw_dict.IsNumericWord(word))


def normalize_word_for_lookup(text):
  """Alias of :func:`normalize_word`; kept for readability at call sites."""
  return normalize_word(text)
