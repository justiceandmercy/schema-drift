# Architecture

schema-drift — Detects drift between declared data schemas and runtime artifacts such as migrations, ORM models, and API payloads, reporting actionable mismatches.

## Overview

The codebase is organized around a small set of focused modules. Each module owns a
single responsibility and the public surface is kept deliberately small so the tool
stays easy to hack on.

## Module map

| Path | Responsibility |
| ---- | -------------- |
| `src/main.py` | CLI entrypoint that bootstraps logging and dispatches to the schema-drift command handlers. |
| `src/schema_drift/cli.py` | Defines argparse subcommands, command handlers, output formatting, and process exit-code policy. |
| `src/schema_drift/config.py` | Loads and validates .schemadrift.toml or YAML configuration, resolving schema, baseline, and ignore paths. |
| `src/schema_drift/models.py` | Defines dataclasses for schemas, tables, fields, constraints, findings, and comparison results. |
| `src/schema_drift/parsers/sql.py` | Parses SQL DDL into the normalized schema graph, including columns, types, defaults, indexes, and foreign keys. |
| `src/schema_drift/parsers/json_schema.py` | Parses JSON Schema and OpenAPI documents into the same normalized model for cross-format comparison. |
| `src/schema_drift/comparator.py` | Implements schema diffing, compatibility checks, rename heuristics, and severity assignment. |
| `src/schema_drift/rules.py` | Runs built-in checks for naming conventions, nullability, defaults, indexes, and constraint consistency. |
| `src/schema_drift/reporters.py` | Renders findings as text, JSON, or SARIF and serializes baseline snapshots for future comparisons. |
| `tests/test_comparator.py` | Unit tests for normalized diffing, rename detection, compatibility modes, and severity classification. |
| `tests/test_cli.py` | Integration tests for init, check, baseline, diff, and explain commands against fixture projects. |
| `pyproject.toml` | Package metadata, dependencies, console entry point, and test configuration. |
| `README.md` | Documents installation, configuration, supported formats, commands, CI usage, and examples. |

## Key design decisions

- **Zero/low runtime dependencies**: the core works from the standard library so
  installs stay fast and supply-chain risk stays low.
- **Configuration before convention**: behavior is tunable through config files and
  documented CLI flags instead of magic environment variables.
- **Structured output modes**: human-readable text by default, machine-readable JSON
  for automation, and DOT/Mermaid exports where a graph applies.
- **Everything is guarded by tests**: the CI matrix runs the suite on every push.

## Data flow

`CLI entrypoint -> config loading -> per-item scanning/processing -> aggregation ->
reporting (text | JSON | graph)`.

## Testing

Unit tests cover schema normalization, diff operations, rename heuristics, compatibility modes, config loading, and CLI exit codes; integration tests run against fixture schemas, migrations, and API payloads.

Run them locally with `pytest -q`. CI (`.github/workflows/ci.yml`) executes the
same suite on a version matrix for every push and pull request.
