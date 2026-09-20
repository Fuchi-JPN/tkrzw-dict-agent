"""Command line interface for the vocabulary sidecar.

Mirrors section 10 of the specification::

    dict lookup tightening
    dict enrich "Fed tightening policy"
    dict extract "long text"

The ``dict`` name is just the installed console script; ``python -m
tkrzw_dict_agent.cli`` works without installing anything.
"""

import argparse
import json
import os
import sys

from ..core import db as db_module, query as query_module

__all__ = ["main", "build_parser", "configure_stdio_encoding"]

DEFAULT_MAX_TERMS = 20


def configure_stdio_encoding():
  """Makes machine-consumed stdio deterministic UTF-8.

  When a stream is redirected (piped to an agent harness or written to a file),
  Python uses the locale encoding.  On Windows that is the ANSI code page --
  cp932 or cp1252 -- so Japanese output reaches the consumer as non-UTF-8 bytes
  and a client that decodes UTF-8 sees mojibake (and UTF-8 input is misread).
  Pinning redirected streams to UTF-8 makes "read the bytes and decode as
  UTF-8" a correct contract in both directions.

  Interactive consoles are left alone: PEP 528 already renders Unicode there,
  and forcing UTF-8 bytes at a cp932 console would break them.  An explicit
  ``PYTHONIOENCODING`` is also respected.
  """
  if os.environ.get("PYTHONIOENCODING"):
    return
  for stream in (sys.stdin, sys.stdout, sys.stderr):
    if stream is None or not hasattr(stream, "reconfigure"):
      continue
    try:
      if stream.isatty():
        continue
      stream.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
      pass


def build_parser():
  # Options shared by every subcommand.  The copies attached to the subparsers
  # use SUPPRESS defaults so that a value given before the subcommand name is
  # not clobbered, which makes both "dict --json lookup w" and
  # "dict lookup w --json" behave the same way.
  common = argparse.ArgumentParser(add_help=False)
  common.add_argument(
      "--data-prefix", default=argparse.SUPPRESS,
      help="Path prefix of the dictionary data (default: the shipped union dictionary).")
  common.add_argument(
      "--json", action="store_true", default=argparse.SUPPRESS,
      help="Emit JSON instead of the hint block.")

  parser = argparse.ArgumentParser(
      prog="dict",
      description="Vocabulary sidecar for AI agents, backed by Tkrzw-Dict.")
  parser.add_argument(
      "--data-prefix", default=os.environ.get("TKRZW_DICT_PREFIX"))
  parser.add_argument("--json", action="store_true")
  subparsers = parser.add_subparsers(dest="command", required=True)

  lookup = subparsers.add_parser(
      "lookup", parents=[common], help="Look up a single word.")
  lookup.add_argument("word", help="The word to look up.")
  lookup.add_argument(
      "--context", default="", help="Context used to rank the candidate meanings.")
  lookup.add_argument(
      "--max-meanings", type=int, default=8, help="Maximum number of meanings.")

  enrich = subparsers.add_parser(
      "enrich", parents=[common],
      help="List Japanese candidates for the difficult words in a text.")
  enrich.add_argument("text", help="The text to enrich, or '-' to read stdin.")
  enrich.add_argument(
      "--max-terms", type=int, default=DEFAULT_MAX_TERMS,
      help="Maximum number of words to explain.")

  extract = subparsers.add_parser(
      "extract", parents=[common], help="Extract the important terms of a text.")
  extract.add_argument("text", help="The text to analyse, or '-' to read stdin.")
  extract.add_argument(
      "--max-terms", type=int, default=DEFAULT_MAX_TERMS,
      help="Maximum number of terms to return.")
  return parser


def main(argv=None):
  configure_stdio_encoding()
  parser = build_parser()
  args = parser.parse_args(argv)
  text = getattr(args, "text", None)
  if text == "-":
    text = sys.stdin.read()
    args.text = text
  with db_module.open_dictionary(args.data_prefix) as dictionary:
    engine = query_module.VocabularyQuery(dictionary)
    if args.command == "lookup":
      return _run_lookup(args, engine)
    if args.command == "enrich":
      return _run_enrich(args, engine)
    if args.command == "extract":
      return _run_extract(args, engine)
  parser.error("unknown command")
  return 2


def _run_lookup(args, engine):
  result = engine.lookup(args.word)
  meanings = engine.resolve(
      args.word, context=args.context, max_meanings=args.max_meanings)
  if args.json:
    result["meanings"] = meanings
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
  if not result["found"]:
    print("{}: not found".format(args.word), file=sys.stderr)
    return 1
  print("{}  {}".format(result["word"], result["pronunciation"]).rstrip())
  for meaning in meanings:
    print("- {}（{}） {}".format(meaning["ja"], meaning["domain"], meaning["score"]))
  return 0


def _run_enrich(args, engine):
  result = engine.enrich(args.text, max_terms=args.max_terms)
  if args.json:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
  if not result["terms"]:
    print("[参考語彙] (no terms found)", file=sys.stderr)
    return 0
  from ..pipelines import enrich as enrich_pipeline

  enrichment = enrich_pipeline.Enrichment(args.text, result["terms"])
  print(enrich_pipeline.format_vocabulary_hints(enrichment))
  return 0


def _run_extract(args, engine):
  terms = engine.extract_terms(args.text, max_terms=args.max_terms)
  if args.json:
    print(json.dumps({"terms": terms}, ensure_ascii=False, indent=2))
    return 0
  for term in terms:
    print(term)
  return 0


if __name__ == "__main__":
  sys.exit(main())
