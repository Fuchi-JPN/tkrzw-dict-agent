"""Server entry point for the vocabulary sidecar HTTP API.

Runs the FastAPI application under uvicorn.  The default host is ``0.0.0.0``
so the endpoint is reachable from other hosts::

    dict-server                          # 0.0.0.0:8765
    dict-server --host 127.0.0.1         # loopback only
    dict-server --port 9000 --workers 4

``python -m tkrzw_dict_agent.agent_api`` is equivalent.

The dictionary is opened lazily on the first request of each worker and kept
memory-mapped for the lifetime of the process.

Security note: the API has no authentication, no CORS policy and no rate
limiting.  Binding to ``0.0.0.0`` exposes it to every host that can reach the
port.  Put it behind a reverse proxy for TLS, authentication and throttling, or
bind to ``127.0.0.1`` and publish it through a tunnel.
"""

import argparse
import os
import sys

__all__ = ["main", "build_parser", "APP_IMPORT_STRING", "DEFAULT_HOST", "DEFAULT_PORT"]

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765

# Passed to uvicorn with factory=True so that --workers and --reload work.
APP_IMPORT_STRING = "tkrzw_dict_agent.agent_api.router:create_app"


def build_parser():
  parser = argparse.ArgumentParser(
      prog="dict-server",
      description="Run the Tkrzw-Dict vocabulary sidecar HTTP API.")
  parser.add_argument(
      "--host", default=os.environ.get("TKRZW_DICT_HOST", DEFAULT_HOST),
      help="Bind address (default: {}).".format(DEFAULT_HOST))
  parser.add_argument(
      "--port", type=int,
      default=int(os.environ.get("TKRZW_DICT_PORT", DEFAULT_PORT)),
      help="Bind port (default: {}).".format(DEFAULT_PORT))
  parser.add_argument(
      "--workers", type=int, default=1,
      help="Number of worker processes (default: 1).")
  parser.add_argument(
      "--log-level", default="info",
      choices=["critical", "error", "warning", "info", "debug", "trace"],
      help="uvicorn log level (default: info).")
  parser.add_argument(
      "--data-prefix", default=os.environ.get("TKRZW_DICT_PREFIX"),
      help="Dictionary data prefix, exported as TKRZW_DICT_PREFIX for the "
           "workers (default: the shipped union dictionary).")
  return parser


def main(argv=None):
  args = build_parser().parse_args(argv)
  try:
    import uvicorn
  except ImportError:
    print(
        "uvicorn is required to serve the API; install it with "
        "'pip install tkrzw-dict-agent[api]'.",
        file=sys.stderr)
    return 2
  if args.data_prefix:
    # Workers are separate processes, so the prefix travels through the
    # environment rather than through the app factory call.
    os.environ["TKRZW_DICT_PREFIX"] = args.data_prefix
  uvicorn.run(
      APP_IMPORT_STRING,
      factory=True,
      host=args.host,
      port=args.port,
      workers=args.workers,
      log_level=args.log_level)
  return 0


if __name__ == "__main__":
  sys.exit(main())
