#!/usr/bin/env python3
"""Evaluate gloss-expansion rules against the audited answers.

The suggestion was that synonym linking (43.3%) plus morphology (7.0%) would
recover most of the X labels.  Those two percentages come from a different
measurement than they appear to:

* **43.3%** is the share of X answers that exist *somewhere* in the dictionary
  as some headword's translation.  That is a coverage measure.  It does **not**
  mean the target word is synonymous with that headword -- measured directly,
  ``runaway`` is not among ``rogue``'s synonyms even though 暴走した is one of
  runaway's glosses.
* **7.0%** is the share of X answers whose morphological base is in the
  dictionary, again dictionary-wide rather than for the target word.

This script measures what actually transfers to the judge, using the 200
audited answers as ground truth.  A conversion is only a gain if the auditor
accepted the answer.

Run:  .venv/bin/python3 scripts/eval_expansion_rules.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexgap import config, db as db_module, normalizer  # noqa: E402
from lexgap.dict_adapter import DictAdapter  # noqa: E402
from tkrzw_dict_agent.core import db as tdb, normalizer as tnorm  # noqa: E402

SUFFIXES = (
    "している", "していた", "させる", "された", "される", "する", "した", "して",
    "的な", "的に", "の", "な", "に", "た", "で", "と", "さ", "み", "的", "性",
    "化", "者", "屋", "式", "風", "用", "済", "中", "後", "前", "付", "付き",
)


def morphology_variants(answer, depth=3):
  """Bounded set of surface forms that should count as the same term."""
  variants = {answer}
  frontier = [answer]
  for _ in range(depth):
    nxt = []
    for current in frontier:
      for suffix in SUFFIXES:
        if current.endswith(suffix) and len(current) > len(suffix):
          trimmed = current[: -len(suffix)]
          if trimmed and trimmed not in variants:
            variants.add(trimmed)
            nxt.append(trimmed)
    frontier = nxt
    if not frontier:
      break
  return variants


def load_audited(db_path):
  conn = db_module.get_conn(db_path)
  rows = conn.execute(
      "SELECT p.headword, p.raw_output, j.label, at.result "
      "FROM audit_tasks at "
      "JOIN probes p ON p.id = at.probe_id "
      "JOIN judgments j ON j.probe_id = p.id "
      "WHERE at.status = 'done' AND at.result IS NOT NULL AND p.task = 'P1'"
  ).fetchall()
  conn.close()
  items = []
  for row in rows:
    verdict = json.loads(row["result"]).get("verdict")
    if verdict in ("accept", "reject"):
      items.append({"headword": row["headword"], "output": row["raw_output"],
                    "label": row["label"], "accept": verdict == "accept"})
  return items


def main():
  items = load_audited(config.DEFAULT_DB_PATH)
  positives = sum(1 for i in items if i["accept"])
  print("audited P1 items: {} (auditor accept {}, reject {})".format(
      len(items), positives, len(items) - positives))

  cache = {}
  with DictAdapter() as adapter, tdb.open_dictionary() as dictionary:

    def data(word):
      if word in cache:
        return cache[word]
      base = {normalizer.normalize(g) for g in adapter.gloss_set(word) if g}
      expanded = {normalizer.normalize(g)
                  for g in dictionary.expand_glosses(word) if g}
      synonym_words = {s.lower() for s in dictionary.synonyms(word, limit=12)}
      cache[word] = (base, expanded, synonym_words)
      return cache[word]

    def judge_accepts(item, rule):
      """True when the rule accepts the answer.

      Every expansion rule is layered *on top of* the baseline verdict: an
      exact gloss match and a partial match must keep counting, otherwise the
      expansion would trade K/P recall for X recovery and lose ground overall.
      """
      answer = normalizer.normalize(item["output"])
      if not answer:
        return False
      baseline = item["label"] in ("K", "P")
      if rule == "baseline":
        return baseline
      base, expanded, synonym_words = data(item["headword"])
      forms = morphology_variants(answer) if "morph" in rule else {answer}
      if rule == "morph_only":
        return any(form in base for form in forms)
      if baseline:
        return True
      if rule in ("expanded", "expanded+morph"):
        return any(form in expanded for form in forms)
      if rule in ("reverse", "reverse+morph"):
        for form in forms:
          hits = dictionary.search_reverse(form, 4)
          if synonym_words & {h.get("word", "").lower() for h in hits if h.get("word")}:
            return True
        return False
      if rule == "all":
        if any(form in expanded for form in forms):
          return True
        for form in forms:
          hits = dictionary.search_reverse(form, 4)
          if synonym_words & {h.get("word", "").lower() for h in hits if h.get("word")}:
            return True
        return False
      raise ValueError(rule)

    print()
    header = "{:<16} {:>8} {:>8} {:>8} {:>8} {:>10}"
    print(header.format("rule", "accept", "recall", "conv+", "fp", "precision"))
    results = {}
    for rule in ("baseline", "morph_only", "expanded", "reverse",
                 "expanded+morph", "reverse+morph", "all"):
      accepted = [i for i in items if judge_accepts(i, rule)]
      good = [i for i in accepted if i["accept"]]
      bad = [i for i in accepted if not i["accept"]]
      baseline_ids = {id(i) for i in items if i["label"] in ("K", "P")}
      converted = [i for i in accepted if id(i) not in baseline_ids]
      precision = len(good) / max(len(accepted), 1)
      results[rule] = (len(accepted), precision, len(converted))
      print(header.format(
          rule, len(accepted), "{:.3f}".format(len(good) / positives),
          len(converted), len(bad), "{:.3f}".format(precision)))

    print()
    print("conversions that were auditor REJECTS (regressions):")
    for rule in ("expanded", "reverse", "all"):
      accepted = [i for i in items if judge_accepts(i, rule)]
      baseline_ids = {id(i) for i in items if i["label"] in ("K", "P")}
      bad = [i for i in accepted if not i["accept"] and id(i) not in baseline_ids]
      print("  {:<10} {}".format(rule, [
          "{} -> {}".format(i["headword"], i["output"]) for i in bad[:5]] or "none"))

    print()
    print("sample conversions (rule=all, auditor accepted):")
    accepted = [i for i in items if judge_accepts(i, "all")]
    baseline_ids = {id(i) for i in items if i["label"] in ("K", "P")}
    for item in [i for i in accepted if i["accept"] and id(i) not in baseline_ids][:10]:
      print("  {:<16} {:<16}".format(item["headword"], item["output"]))


if __name__ == "__main__":
  main()
