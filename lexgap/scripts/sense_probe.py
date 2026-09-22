#!/usr/bin/env python3
"""Sense-level probe: does the model pick the sense the context implies?

M1 measured whether an answer was an acceptable translation of the word -- it
compared against every sense's glosses at once.  A model that answers 銀行 for
"the river bank" would therefore have counted as correct, because 銀行 is one
of the word's translations.

This experiment separates the two questions.  Each sense definition carries its
own example after "e.g.:", and 76% of polysemous words have at least one sense
whose example contains the headword.  Those examples are sense-tagged contexts,
so a probe built from one has a known intended sense.  An answer is then scored
three ways:

  sense_ok   the answer is a gloss of the intended sense      (what matters)
  other_sense the answer is a gloss of a different sense      (wrong sense)
  neither    the answer is not a gloss of any sense

Run:  .venv/bin/python3 scripts/sense_probe.py [--limit N]
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexgap import config, db as db_module, normalizer, prompter  # noqa: E402
from lexgap.dict_adapter import DictAdapter  # noqa: E402

EG_PATTERN = re.compile(r"e\.g\.:\s*(.*?)(?=\[-\]|\Z)", re.DOTALL)


def usable_senses(record):
  """Senses that have both a sense-specific example and translations."""
  word_pattern = re.compile(r"\b" + re.escape(record.word) + r"\b", re.IGNORECASE)
  out = []
  for index, sense in enumerate(record.senses):
    match = EG_PATTERN.search(sense.text or "")
    if not match:
      continue
    example = match.group(1).strip()
    if not example or not word_pattern.search(example):
      continue
    if not sense.translations:
      continue
    out.append({"index": index, "example": example,
                "translations": {normalizer.normalize(t)
                                 for t in sense.translations if t}})
  return out


def collect(limit, log, min_senses=3, min_length=4, min_rank=3000):
  """Finds content words with several senses that each carry their own example.

  The dictionary is ordered by frequency, so the head of the list is function
  words -- "be", "in", "on" -- which are useless as disambiguation targets.
  Those are skipped, as are short words, so the sample is content words where
  picking the wrong sense is actually possible.
  """
  from tkrzw_dict_agent.core import normalizer as parent_normalizer

  picked = []
  with DictAdapter() as adapter:
    for record in adapter.iter_headwords(ascii_only=True, require_translation=True):
      if record.n_senses < min_senses:
        continue
      if len(record.word) < min_length:
        continue
      if parent_normalizer.is_stop_word("en", record.word.lower()):
        continue
      # Skip the very head of the frequency list: those are grammaticalised
      # words ("make", "take", "point") whose senses are not separable by a
      # single context sentence in any reliable way.
      if record.freq_rank < min_rank:
        continue
      usable = usable_senses(record)
      if len(usable) < 2:
        continue
      # The two senses must be genuinely different, or the test is vacuous.
      if usable[0]["translations"] == usable[1]["translations"]:
        continue
      picked.append({"word": record.word, "senses": usable})
      if limit and len(picked) >= limit:
        break
  log.info("collected %d content words with >=2 sense-tagged examples", len(picked))
  return picked


async def probe_all(items, log):
  import asyncio
  import httpx

  runners = config.load_runners()["runner"]
  runner = runners["primary"]
  base_url = runner["base_url"].rstrip("/")
  model = runner["default_model"]["name"]
  concurrency = int(runner.get("max_concurrency", 2))
  kwargs = {}
  if runner["capabilities"].get("supports_disable_thinking"):
    kwargs["chat_template_kwargs"] = runner["capabilities"].get(
        "disable_thinking_kwargs", {"enable_thinking": False})
  semaphore = asyncio.Semaphore(concurrency)

  async def one(client, item):
    async with semaphore:
      body = {"model": model,
              "messages": [{"role": "user", "content": item["prompt"]}],
              "temperature": 0, "max_tokens": 32, "seed": 42}
      body.update(kwargs)
      try:
        response = await client.post(base_url + "/chat/completions", json=body)
        if response.status_code != 200:
          return item, None
        choice = (response.json().get("choices") or [{}])[0]
        return item, ((choice.get("message") or {}).get("content") or "").strip()
      except Exception as exc:  # noqa: BLE001
        log.warning("probe failed: %s", exc)
        return item, None

  tasks = []
  async with httpx.AsyncClient(timeout=300) as client:
    tasks = [asyncio.create_task(one(client, item)) for item in items]
    results = []
    for finished in asyncio.as_completed(tasks):
      results.append(await finished)
  return results


def score(answer, item):
  """Classifies the answer against the intended and the other senses."""
  normalized = normalizer.normalize(answer)
  intended = item["sense"]["translations"]
  others = set()
  for other in item["all_senses"]:
    if other is not item["sense"]:
      others |= other["translations"]
  if not normalized:
    return "empty"
  if normalized in intended:
    return "sense_ok"
  for form in normalizer.japanese_variants(answer):
    if form in intended:
      return "sense_ok"
  if normalized in others:
    return "other_sense"
  for form in normalizer.japanese_variants(answer):
    if form in others:
      return "other_sense"
  return "neither"


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--limit", type=int, default=40,
                      help="Words to collect (each is probed once per sense).")
  args = parser.parse_args()

  import logging
  logging.basicConfig(level=logging.INFO,
                      format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                      stream=sys.stderr)
  log = logging.getLogger("sense_probe")

  words = collect(args.limit, log)
  items = []
  for entry in words:
    for sense in entry["senses"][:2]:   # two senses per word is enough
      marked = prompter.mark_target(sense["example"], entry["word"])
      items.append({
          "word": entry["word"],
          "sense": sense,
          "all_senses": entry["senses"],
          "prompt": prompter.render("P1", entry["word"], marked),
      })
  log.info("probing %d sense-tagged contexts", len(items))

  results = asyncio_run(probe_all(items, log))
  counts = {"sense_ok": 0, "other_sense": 0, "neither": 0, "empty": 0}
  rows = []
  for item, answer in results:
    verdict = score(answer or "", item) if answer is not None else "empty"
    counts[verdict] = counts.get(verdict, 0) + 1
    rows.append((item["word"], answer, verdict))

  total = max(len(results), 1)
  print()
  print("=== sense-level probe ({} contexts) ===".format(len(results)))
  for key in ("sense_ok", "other_sense", "neither", "empty"):
    print("  {:<12} {:>4}  ({:.1f}%)".format(
        key, counts[key], counts[key] / total * 100))
  print()
  print("  sense_ok = correct sense | other_sense = wrong sense of the same word")
  print()
  print("examples of wrong-sense answers:")
  shown = 0
  for word, answer, verdict in rows:
    if verdict in ("other_sense", "neither") and shown < 12:
      print("    {:<14} {:<16} {}".format(word, (answer or "")[:16], verdict))
      shown += 1


def asyncio_run(coro):
  import asyncio
  return asyncio.run(coro)


if __name__ == "__main__":
  main()
