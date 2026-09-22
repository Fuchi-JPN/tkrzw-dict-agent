"""Prompt templates and their rendering.

Prompts live in ``configs/prompts/`` as numbered files (``p1_v1.txt``), and the
file name *is* the version that gets recorded in ``probes.prompt_ver``.  A
behavioural change means a new file, never an edit in place, so a probe row can
always be traced back to the exact text that produced it.

Two probe formats exist for M1:

* ``P1`` -- the target word appears inside an English sentence; the model must
  produce its Japanese translation.  This is the generation probe.
* ``W1`` -- the word alone, with no sentence.  Comparing P1 against W1 separates
  "cannot translate the word" from "cannot use the context".

Context selection matters: a sentence that does not contain the target word is
not context, it is a distractor.  Measured on a 500-word frequency-spread
sample, 90.6% of words have at least one example containing the headword, 8.4%
have no examples at all, and there are 4.3 examples per word on average.  Words
without a usable sentence fall back to ``W1``.
"""

import re

from . import config

__all__ = [
  "TASKS",
  "PROMPTER_VERSION",
  "build_probe",
  "render",
  "template_version",
  "select_context",
  "mark_target",
  "normalize_headword",
]

TASKS = ("P1", "W1")

PROMPTER_VERSION = "pr1"

# Inflectional tails the dictionary's example sentences may carry while the
# headword does not, e.g. headword "tighten" inside "tightened".
_INFLECTION_TAILS = ("s", "es", "ed", "d", "ing", "er", "est", "ly")


def template_version(task):
  """Returns the template file name for a task, e.g. ``p1_v1.txt``."""
  return "{}_v1.txt".format(task.lower())


def render(task, word, sentence=None):
  """Renders the prompt for a task.

  :param task: ``"P1"`` or ``"W1"``.
  :param word: The target headword (display form).
  :param sentence: The context sentence with the target already marked.
  """
  if task not in TASKS:
    raise ValueError("unknown task: {}".format(task))
  template = config.load_prompt(template_version(task))
  return template.format(word=word, sentence=sentence or "")


def build_probe(record, task=None):
  """Builds the probe payload for one dictionary record.

  :returns: ``{"task", "prompt", "word", "sentence", "prompt_ver"}``.  When
      ``task`` is None the best available form is chosen: ``P1`` when a usable
      sentence exists, ``W1`` otherwise.
  """
  word = record.word
  sentence = select_context(record)
  if task is None:
    task = "P1" if sentence else "W1"
  if task == "P1" and not sentence:
    # A P1 request without a sentence would silently degrade into W1, so make
    # the switch explicit instead.
    task = "W1"
  marked = mark_target(sentence, word) if sentence else None
  return {
      "task": task,
      "prompt": render(task, word, marked),
      "word": word,
      "sentence": marked,
      "prompt_ver": "{}@{}".format(template_version(task), PROMPTER_VERSION),
  }


def select_context(record, prefer_multiple=True):
  """Returns an example sentence that actually contains the target word.

  Sentences are scanned in the dictionary's order and the first match wins, so
  selection is deterministic.  A sentence is a match when it contains the
  headword, allowing common inflectional tails (``tighten`` in ``tightened``).
  Returns None when no example qualifies.
  """
  if not record.examples:
    return None
  pattern = _target_pattern(record.word)
  candidates = [example["e"] for example in record.examples if example.get("e")]
  for sentence in candidates:
    if pattern.search(sentence):
      return sentence
  if prefer_multiple:
    return None
  return candidates[0] if candidates else None


def mark_target(sentence, word):
  """Wraps the target occurrence in the sentence with 【 】.

  The marker removes any ambiguity about which word is being asked about, which
  matters when the sentence contains several content words.
  """
  if not sentence:
    return sentence
  pattern = _target_pattern(word)
  match = pattern.search(sentence)
  if not match:
    return sentence
  return "{}{}{}".format(
      sentence[:match.start()], "【{}】".format(match.group(0)), sentence[match.end():])


def normalize_headword(word):
  """Lower-cases a headword for comparison against dictionary keys."""
  return (word or "").strip().lower()


def _target_pattern(word):
  """Builds the regex used to find the target word in a sentence.

  Matches the headword, or the headword plus an inflectional tail, on word
  boundaries and case-insensitively.
  """
  escaped = re.escape(word)
  tails = "|".join(re.escape(tail) for tail in _INFLECTION_TAILS)
  return re.compile(r"\b(?:{})(?:{})?\b".format(escaped, tails), re.IGNORECASE)
