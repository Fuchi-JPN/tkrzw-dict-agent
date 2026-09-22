#!/usr/bin/env python3
"""M0 investigation: frequency fields (Q-01) and example/sense mapping (Q-02).

Scans every record of the union dictionary once and reports, per candidate
frequency field, the presence rate and value range; and, for the example field,
whether examples can be tied to a specific sense.

Run:  ../.venv/bin/python3 scripts/m0_investigate.py
"""

import collections
import io
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tkrzw_dict_agent.core import pytkrzw  # noqa: E402

DICT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "tkrzw-dict")
BODY = os.path.join(DICT_DIR, "union-body.tkh")
KEYS = os.path.join(DICT_DIR, "union-keys.txt")

FREQ_CANDIDATES = ("probability", "aoa_base", "aoa_concept", "share")


def main():
  dbm = pytkrzw.DBM()
  dbm.Open(BODY, False, dbm="HashDBM").OrDie()
  keys = [line.rstrip("\n") for line in io.open(KEYS, encoding="utf-8") if line.strip()]
  print("keys: {}  (body records: {})".format(len(keys), dbm.Count()))

  presence = {name: 0 for name in FREQ_CANDIDATES}
  values = {name: [] for name in FREQ_CANDIDATES}
  all_field_names = collections.Counter()
  example_presence = 0
  example_total = 0
  example_with_joiner = 0
  item_presence = 0
  entries_total = 0
  sample_examples = []
  parse_errors = 0

  t0 = time.perf_counter()
  for index, key in enumerate(keys):
    serialized = dbm.GetStr(key)
    if not serialized:
      continue
    try:
      entries = json.loads(serialized)
    except json.JSONDecodeError:
      parse_errors += 1
      continue
    for entry in entries:
      entries_total += 1
      for name in entry:
        all_field_names[name] += 1
      for name in FREQ_CANDIDATES:
        raw = entry.get(name)
        if raw is not None and raw != "":
          presence[name] += 1
          try:
            values[name].append(float(raw))
          except (TypeError, ValueError):
            pass
      if entry.get("item"):
        item_presence += 1
      examples = entry.get("example")
      if examples:
        example_presence += 1
        if isinstance(examples, list):
          for example in examples:
            example_total += 1
            if isinstance(example, dict) and "e" in example and "j" in example:
              # A sense joiner marker would indicate sense-scoped examples.
              if "[-]" in example.get("e", "") or "sense" in example:
                example_with_joiner += 1
              elif len(sample_examples) < 4:
                sample_examples.append((key, example))
    if index and index % 100000 == 0:
      print("  ... {} / {}".format(index, len(keys)))
  elapsed = time.perf_counter() - t0
  dbm.Close()

  print()
  print("=== M0-2 scan timing ===")
  print("entries scanned: {}".format(entries_total))
  print("elapsed: {:.1f}s  ({:.0f} us/key, {:.0f} entries/s)".format(
      elapsed, elapsed / len(keys) * 1e6, entries_total / elapsed))
  print("json parse errors: {}".format(parse_errors))

  print()
  print("=== Q-01 frequency fields ===")
  for name in FREQ_CANDIDATES:
    count = presence[name]
    rate = count / entries_total if entries_total else 0.0
    if values[name]:
      vals = sorted(values[name])
      print("  {:<12} present {:>7}/{:<7} ({:5.1f}%)  min={:.3g} median={:.3g} max={:.3g}".format(
          name, count, entries_total, rate * 100,
          vals[0], statistics.median(vals), vals[-1]))
    else:
      print("  {:<12} present {:>7}/{:<7} ({:5.1f}%)  (no numeric values)".format(
          name, count, entries_total, rate * 100))

  print()
  print("=== Q-02 examples ===")
  print("entries with item:   {} ({:.1f}%)".format(
      item_presence, item_presence / max(entries_total, 1) * 100))
  print("entries with example: {} ({:.1f}%)".format(
      example_presence, example_presence / max(entries_total, 1) * 100))
  print("example objects:      {}".format(example_total))
  print("examples with an in-text sense marker: {}".format(example_with_joiner))
  print("sample examples:")
  for key, example in sample_examples:
    print("   [{}] {!r}".format(key, example))

  print()
  print("=== record fields (top 25 by presence) ===")
  for name, count in all_field_names.most_common(25):
    print("  {:<22} {:>8} ({:5.1f}%)".format(
        name, count, count / max(entries_total, 1) * 100))


if __name__ == "__main__":
  main()
