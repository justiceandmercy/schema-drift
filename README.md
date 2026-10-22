# schema-drift

[![CI](https://github.com/justiceandmercy/schema-drift/actions/workflows/ci.yml/badge.svg)](https://github.com/justiceandmercy/schema-drift/actions/workflows/ci.yml)


[![CI](https://github.com/justiceandmercy/schema-drift/actions/workflows/ci.yml/badge.svg)](https://github.com/justiceandmercy/schema-drift/actions/workflows/ci.yml)


Detects drift between declared data schemas and runtime artifacts such as migrations, ORM models, and API payloads, reporting actionable mismatches.

[![CI](https://img.shields.io/badge/CI-passing-brightgreen)]() [![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)]() [![License: MIT](https://img.shields.io/badge/license-MIT-green)]()

## Why schema-drift?

In a growing codebase the canonical schema declared in SQL DDL, JSON Schema, or OpenAPI drifts away from what migrations, ORM classes, and API fixtures actually ship. `schema-drift` parses all of these into one normalized intermediate model, compares them across git revisions and runtime artifacts, and reports added, removed, renamed, and type-changed fields with severity and suggested remediation. It is built for CI pipelines and code review: stable exit codes, human-readable / JSON / SARIF output, caching for monorepos, and configurable ignore rules.

## Features

- Parses canonical schema files in **SQL DDL**, **JSON Schema**, and **OpenAPI** into a normalized intermediate model.
- Compares schema snapshots across **git revisions**, **migrations**, **ORM classes**, and **API fixtures**.
- Reports **added**, **removed**, **renamed**, and **type-changed** fields with severity and suggested remediation.
- Supports configurable **ignore rules**, **aliases**, and **compatibility modes** (additive-only changes).
- Emits **human-readable**, **JSON**, and **SARIF** reports for CI pipelines and code review.
- Caches parsed schema graphs to speed up repeated checks in monorepos.
- Subcommands: `init`, `check`, `baseline`, `diff`, `explain`.
- Validates naming conventions, nullability, defaults, indexes, and foreign-key constraints.
- Integrates with **pre-commit** and **GitHub Actions** through stable exit codes.

## Installation

pip install schema-drift

Or with [pipx](https://pipx.pypa.io/) for an isolated CLI:

pipx install schema-drift

Requires Python 3.10 or newer.

Verify installation:

schema-drift --version

## Quick start

# 1. Scaffold a configuration file in the current project
cd my-project
schema-drift init

# 2. Declare your canonical schema (example: SQL DDL)
cat > schema.sql <<'SQL'
CREATE TABLE users (
  id          BIGINT PRIMARY KEY,
  email       TEXT NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT now()
);
SQL

# 3. Run a check against the working tree
schema-drift check

Example output (human-readable):

[ERROR] users.email: removed in migration 0003_add_users.sql
  expected: TEXT NOT NULL
  found:    (missing)
  hint:     add column back or update canonical schema

Summary: 1 error, 0 warnings, 0 info
exit code: 1

## Supported formats

| Format | Detection | Notes |
| --- | --- | --- |
| SQL DDL | `.sql` | `CREATE TABLE`, columns, types, nullability, defaults, PK/FK, indexes |
| JSON Schema | `.json` (with `$schema`) | object properties, `required`, types, formats, enums |
| OpenAPI | `.yaml`/`.json` (with `openapi`) | request/response schemas under `components/schemas` |
| ORM models | Python modules | dataclasses, SQLAlchemy, Django/Pydantic (via adapters) |
| Migrations | `.sql`/`.py` | Alembic, Django, raw SQL migrations |
| API fixtures | `.json` | sample payloads validated against declared schema |

## Configuration

`schemadrift.toml` (or `.schemadrift.toml`) at the project root:

[project]
name = "my-service"
canonical = ["schema/**/*.sql", "openapi.yaml"]

[runtime]
migrations = "migrations/"
models = "app/models/"
fixtures = "tests/fixtures/api/"

[compatibility]
mode = "additive"          # additive | strict | backward
allow_renames = true
allow_type_widening = true

[ignore]
paths = ["migrations/legacy/**"]
rules = ["naming.snake_case", "default.now"]

[aliases]
"users.id" = "accounts.user_id"

[cache]
enabled = true
dir = ".schemadrift-cache"

[report]
format = "human"           # human | json | sarif
severity_threshold = "warning"

Environment overrides: `SCHEMA_DRIFT_CONFIG`, `SCHEMA_DRIFT_CACHE_DIR`, `SCHEMA_DRIFT_FORMAT`.

## Commands

### `schema-drift init`

Scaffold a default `schemadrift.toml`, detect likely canonical/runtime paths, and print next steps.

schema-drift init [--path PATH] [--force]

### `schema-drift check`

Run a full drift check against the working tree (or a revision).

schema-drift check [PATH] \
  --config PATH \
  --format human|json|sarif \
  --severity error|warning|info \
  --fail-on error|warning|never \
  --cache/--no-cache

### `schema-drift baseline`

Capture the current state as an accepted baseline so only *new* drift is reported.

schema-drift baseline [--output baseline.json] [--revision REV]

### `schema-drift diff`

Compare two revisions or two artifacts directly.

schema-drift diff REV_A REV_B
schema-drift diff --from schema.sql --to models.py

### `schema-drift explain`

Explain a single reported mismatch with remediation steps.

schema-drift explain RULE_ID [PATH]

Run `schema-drift <command> --help` for full option lists.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | No drift above the configured threshold |
| `1` | Drift detected (errors) |
| `2` | Warnings only (when `--fail-on warning`) |
| `3` | Configuration or input error |
| `4` | Internal error |

These are stable and safe to branch on in CI.

## Report formats

- **human**: colored, grouped by entity, with hints.
- **json**: machine-readable object with `findings[]`, `summary`, `exit_code`.
- **sarif**: SARIF 2.1.0 for GitHub Code Scanning / PR annotations.

Example JSON finding:

{
  "rule": "field.removed",
  "severity": "error",
  "entity": "users",
  "field": "email",
  "message": "field present in canonical schema but missing in migration 0003",
  "remediation": "add column `email TEXT NOT NULL` or update canonical schema",
  "locations": [
    { "path": "schema.sql", "line": 4 },
    { "path": "migrations/0003.sql", "line": 12 }
  ]
}

## CI usage

### GitHub Actions

name: schema-drift
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install schema-drift
      - run: schema-drift check --format sarif --fail-on error > results.sarif
      - uses: github/codeql-action/upload-sarif@v3
        with: { sarif_file: results.sarif }

### pre-commit

repos:
  - repo: https://github.com/example/schema-drift
    rev: v0.1.0
    hooks:
      - id: schema-drift
        args: ["check", "--fail-on", "error"]

## Caching

Parsed schema graphs are cached under `.schemadrift-cache/` (configurable). Cache keys include file content hashes and parser version, so stale results are never reused. Disable with `--no-cache` or `cache.enabled = false`. In monorepos, point `cache.dir` at a shared location to reuse across packages.

## Ignore rules, aliases, compatibility

- **Ignore rules**: skip paths or rule IDs (`ignore.paths`, `ignore.rules`).
- **Aliases**: map renamed fields across sources (`aliases` table) so renames are not reported as remove+add.
- **Compatibility modes**:
  - `additive` (default): only additive changes allowed; removals/type-narrowing are errors.
  - `strict`: any difference is an error.
  - `backward`: removals allowed, additions of required fields are errors.

## Validation rules

Naming conventions, nullability, defaults, indexes, and foreign-key constraints are validated and reported with stable rule IDs such as `naming.snake_case`, `nullability.required_added`, `fk.missing_index`, `default.incompatible`.

## Development

git clone https://github.com/example/schema-drift
cd schema-drift
pip install -e ".[dev]"
pytest -q
ruff check .
mypy src

## License

MIT. See `LICENSE`.
