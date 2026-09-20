"""Runnable module: ``python -m tkrzw_dict_agent.agent_api``.

Runs the HTTP API with the same defaults as the ``dict-server`` console script
(host ``0.0.0.0``, port 8765).
"""

import sys

from .server import main

if __name__ == "__main__":
  sys.exit(main())
