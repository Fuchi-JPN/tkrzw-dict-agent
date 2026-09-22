#!/usr/bin/env python3
"""M0 environment check.

Runs the plan's start-up checklist and exits non-zero if any item fails.

    python3 scripts/m0_envcheck.py

The implementation lives in ``lexgap.diagnostics`` so the CLI command
``lexgap envcheck`` and this script stay in step.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexgap import config, diagnostics  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stderr)


def main():
  log = logging.getLogger("m0_envcheck")
  config.ensure_dirs()
  return diagnostics.run_envcheck(log, db_path=config.DEFAULT_DB_PATH)


if __name__ == "__main__":
  sys.exit(main())
