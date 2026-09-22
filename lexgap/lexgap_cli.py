"""Command line entry point for LexGap.

Every subcommand accepts ``--db`` (default ``data/lexgap.db``) and
``--verbose``.  Commands are idempotent and resumable: re-running the same
command continues from the cached state rather than starting over.

M0 implements ``init-db``, ``dict-stats`` and ``envcheck``.  The experiment
commands (``sample``, ``probe``, ``judge``, ``audit``, ``fit``, ``export``,
``intervene``, ``report``) are added with their milestones, so the CLI never
advertises a command that does nothing.
"""

import argparse
import logging
import sys

from lexgap import __version__, config, db as db_module, diagnostics

__all__ = ["main", "build_parser", "setup_logging"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose=False, name="lexgap"):
  """Configures root logging once and returns the module logger."""
  level = logging.DEBUG if verbose else logging.INFO
  logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stderr)
  return logging.getLogger(name)


def build_parser():
  parser = argparse.ArgumentParser(
      prog="lexgap",
      description="Japanese vocabulary-gap verification for small LLMs.")
  parser.add_argument("--version", action="version", version="lexgap " + __version__)
  parser.add_argument(
      "--db", default=None,
      help="SQLite database path (default: data/lexgap.db).")
  parser.add_argument(
      "--verbose", action="store_true", help="Enable debug logging.")

  subparsers = parser.add_subparsers(dest="command", required=True)

  subparsers.add_parser(
      "init-db", help="Create or verify the SQLite schema (idempotent).")

  dict_stats = subparsers.add_parser(
      "dict-stats", help="Report dictionary coverage and the M0 field findings.")
  dict_stats.add_argument(
      "--sample", type=int, default=0,
      help="Scan only this many keys (0 = all).")
  dict_stats.add_argument(
      "--json", action="store_true", help="Emit JSON instead of text.")

  subparsers.add_parser(
      "envcheck", help="Verify the M0 environment checklist.")

  return parser


def main(argv=None):
  parser = build_parser()
  args = parser.parse_args(argv)
  log = setup_logging(args.verbose)
  db_path = config.resolve_db_path(args.db)

  if args.command == "init-db":
    return _cmd_init_db(log, db_path)
  if args.command == "dict-stats":
    return diagnostics.run_dict_stats(log, sample=args.sample, as_json=args.json)
  if args.command == "envcheck":
    return diagnostics.run_envcheck(log, db_path=db_path)
  parser.error("unknown command")
  return 2


def _cmd_init_db(log, db_path):
  conn = db_module.get_conn(db_path)
  version = conn.execute("PRAGMA user_version").fetchone()[0]
  tables = [row["name"] for row in conn.execute(
      "SELECT name FROM sqlite_master WHERE type='table' "
      "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
  conn.close()
  log.info("database ready: %s (schema v%d, %d tables)",
           db_path, version, len(tables))
  for name in tables:
    print(name)
  return 0


if __name__ == "__main__":
  sys.exit(main())
