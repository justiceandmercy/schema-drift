"""CLI entrypoint for schema-drift."""

from __future__ import annotations

import logging
import os
import sys

from schema_drift.cli import main as cli_main


def configure_logging() -> None:
    """Configure process-wide logging from environment variables."""
    level = os.getenv("SCHEMA_DRIFT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    """Bootstrap logging and dispatch to the command-line interface."""
    configure_logging()
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
