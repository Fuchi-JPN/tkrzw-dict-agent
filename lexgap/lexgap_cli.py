"""Command line entry point for LexGap.

Every subcommand accepts ``--db`` (default ``data/lexgap.db``) and
``--verbose``.  Commands are idempotent and resumable: re-running the same
command continues from cached state rather than starting over, which is what
makes a multi-hour probe batch safe to interrupt.

M0 provides ``init-db``, ``dict-stats`` and ``envcheck``.  M1 adds the pipeline
commands ``sample``, ``probe``, ``judge``, ``audit`` and ``report``.
"""

import argparse
import logging
import sys

from lexgap import (
  __version__,
  config,
  db as db_module,
  diagnostics,
)

__all__ = ["main", "build_parser", "setup_logging"]

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DEFAULT_SET = "S1"
DEFAULT_LAYER = "main"
DEFAULT_TASK = "P1"


def setup_logging(verbose=False, name="lexgap"):
  """Configures root logging once and returns the module logger.

  httpx logs one INFO line per request, which drowns out the probe progress at
  two thousand probes, so it is quietened unless --verbose is given.
  """
  level = logging.DEBUG if verbose else logging.INFO
  logging.basicConfig(level=level, format=LOG_FORMAT, stream=sys.stderr)
  if not verbose:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
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
  _add_m0_commands(subparsers)
  _add_pipeline_commands(subparsers)
  return parser


def _add_m0_commands(subparsers):
  subparsers.add_parser(
      "init-db", help="Create or verify the SQLite schema (idempotent).")

  dict_stats = subparsers.add_parser(
      "dict-stats", help="Report dictionary coverage and the M0 field findings.")
  dict_stats.add_argument("--sample", type=int, default=0,
                          help="Scan only this many keys (0 = all).")
  dict_stats.add_argument("--json", action="store_true",
                          help="Emit JSON instead of text.")

  subparsers.add_parser("envcheck", help="Verify the M0 environment checklist.")


def _add_pipeline_commands(subparsers):
  sample = subparsers.add_parser(
      "sample", help="Build a sample set from a spec (M1).")
  sample.add_argument("--set", default=DEFAULT_SET, help="Spec/set name.")
  sample.add_argument("--limit", type=int, default=None,
                      help="Cap the number of scanned keys (smoke runs).")
  sample.add_argument("--replace", action="store_true",
                      help="Delete an existing set of the same id first.")

  probe = subparsers.add_parser(
      "probe", help="Run probes for a sample set (M1).")
  probe.add_argument("--set", default=DEFAULT_SET)
  probe.add_argument("--task", action="append", default=None,
                     help="Task(s) to run; repeatable (default: the spec's tasks).")
  probe.add_argument("--layer", action="append", default=None,
                     help="Sample layer(s); repeatable (default: main).")
  probe.add_argument("--runner", default="primary", help="Runner name.")
  probe.add_argument("--limit", type=int, default=None,
                     help="Cap the number of samples (smoke runs).")
  probe.add_argument("--seed", type=int, default=42)
  probe.add_argument("--rerun-errors", action="store_true",
                     help="Retry probes that failed all attempts earlier.")

  judge = subparsers.add_parser(
      "judge", help="Judge stored probe outputs (M1).")
  judge.add_argument("--set", default=DEFAULT_SET)
  judge.add_argument("--task", default=None, help="Restrict to one task.")
  judge.add_argument("--limit", type=int, default=None)

  audit = subparsers.add_parser(
      "audit", help="Queue and run the LLM audit (M1).")
  audit.add_argument("--set", default=DEFAULT_SET)
  audit.add_argument("--runner", default="primary")
  audit.add_argument("--per-layer", type=int, default=10,
                     help="Audit tasks per frequency layer (default: 10 for 200 total).")
  audit.add_argument("--limit", type=int, default=None,
                     help="Cap the number of audit calls this run.")
  audit.add_argument("--queue-only", action="store_true",
                     help="Only queue tasks; do not call the auditor.")

  report = subparsers.add_parser(
      "report", help="Frequency-stratified correctness curve (M1 / H1).")
  report.add_argument("--set", default=DEFAULT_SET)
  report.add_argument("--task", default=None, help="Restrict to one task.")
  report.add_argument("--out", default=None, help="Write the Markdown here.")
  report.add_argument("--csv", default=None, help="Write the CSV here.")


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

  config.ensure_dirs()
  if args.command == "sample":
    return _cmd_sample(log, db_path, args)
  if args.command == "probe":
    return _cmd_probe(log, db_path, args)
  if args.command == "judge":
    return _cmd_judge(log, db_path, args)
  if args.command == "audit":
    return _cmd_audit(log, db_path, args)
  if args.command == "report":
    return _cmd_report(log, db_path, args)

  parser.error("unknown command")
  return 2


# -- M0 --------------------------------------------------------------------
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


# -- M1 --------------------------------------------------------------------
def _cmd_sample(log, db_path, args):
  from lexgap import sampler

  spec = config.load_spec(args.set)
  conn = db_module.get_conn(db_path)
  try:
    if args.replace:
      with db_module.transaction(conn):
        conn.execute("DELETE FROM samples WHERE set_id = ?", (spec["set"]["id"],))
        conn.execute("DELETE FROM sample_sets WHERE set_id = ?", (spec["set"]["id"],))
      log.info("replaced existing set %s", spec["set"]["id"])
    summary = sampler.generate(conn, spec, log, limit=args.limit)
  except sampler.SampleError as exc:
    log.error("%s", exc)
    return 1
  finally:
    conn.close()

  print("set:                 {}".format(summary["set_id"]))
  print("seed:                {}".format(summary["seed"]))
  print("eligible words:      {:,}".format(summary["eligible"]))
  print("excluded (no gloss): {:,}".format(summary["excluded_no_translation"]))
  print("main layer:          {:,}".format(summary["main"]))
  print("multi-sense layer:   {:,}".format(summary["multi_sense"]))
  print("domain layer:        {:,}".format(summary["domain"]))
  print("total rows:          {:,}".format(summary["total"]))
  fill = summary["quantile_fill"]
  short = [q for q, n in fill.items() if n < int(spec["main"]["per_quantile"])]
  print("quantiles short of target: {}".format(short if short else "none"))
  return 0


def _cmd_probe(log, db_path, args):
  from lexgap import runner

  spec = config.load_spec(args.set)
  tasks = tuple(args.task) if args.task else tuple(spec.get("probe", {}).get("tasks", ("P1",)))
  layers = tuple(args.layer) if args.layer else (DEFAULT_LAYER,)
  conn = db_module.get_conn(db_path)
  try:
    engine = runner.ProbeRunner(conn, runner_name=args.runner, log=log)
    summary = engine.run(
        args.set, tasks=tasks, layers=layers, limit=args.limit,
        seed=args.seed, rerun_errors=args.rerun_errors)
  finally:
    conn.close()
  print(summary.as_dict())
  return 0 if summary.failed == 0 else 1


def _cmd_judge(log, db_path, args):
  from lexgap import judge

  conn = db_module.get_conn(db_path)
  try:
    summary = judge.judge_set(conn, args.set, _model_id(), task=args.task,
                              log=log, limit=args.limit)
  finally:
    conn.close()
  print(summary)
  return 0


def _cmd_audit(log, db_path, args):
  from lexgap import audit, judge, runner

  conn = db_module.get_conn(db_path)
  try:
    queued = judge.queue_audit_tasks(
        conn, args.set, _model_id(), per_layer=args.per_layer, log=log)
    counts = {"accept": 0, "reject": 0, "unknown": 0}
    if not args.queue_only and queued:
      engine = runner.ProbeRunner(conn, runner_name=args.runner, log=log)
      counts = audit.run_audit(conn, engine, log=log, limit=args.limit)
    result = audit.summary(conn)
  finally:
    conn.close()
  print("queued:  {}".format(queued))
  print("verdict: {}".format(counts))
  print("summary: {}".format(result))
  return 0


def _cmd_report(log, db_path, args):
  from lexgap import reporter

  conn = db_module.get_conn(db_path)
  try:
    curve, totals, markdown = reporter.run_report(
        conn, args.set, _model_id(), task=args.task, log=log, csv_path=args.csv)
  finally:
    conn.close()
  if args.out:
    with open(args.out, "w", encoding="utf-8") as handle:
      handle.write(markdown + "\n")
    log.info("wrote %s", args.out)
  print(markdown)
  return 0 if totals["n"] else 1


def _model_id():
  """The model id recorded in the database (from the primary runner)."""
  runners = config.load_runners()["runner"]
  runner = runners.get("primary") or runners.get("local")
  return runner.get("model_id") or "unknown"


if __name__ == "__main__":
  sys.exit(main())
