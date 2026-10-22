"""Command-line interface for schema-drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from schema_drift.config import ConfigError, load_config
from schema_drift.models import Schema, compare_schemas
from schema_drift.parsers.json_schema import JsonSchemaParser, parse_json_schema
from schema_drift.parsers.sql import SQLParser, parse_sql

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

SUPPORTED_SUFFIXES = {".sql", ".json", ".yaml", ".yml"}

EXPLANATIONS = {
    "added": "A new schema element exists in the target but not in the baseline. In additive mode this is allowed; otherwise update consumers and migrations.",
    "removed": "A schema element disappeared. Restore it or add a migration and update all callers.",
    "renamed": "A field was renamed. Keep a compatibility alias until clients migrate.",
    "type_changed": "A type changed. Add a casting migration or revert the type change.",
    "nullability_changed": "Nullability changed. Check application code and backfill existing rows.",
    "default_changed": "A default changed. Verify application assumptions and migration defaults.",
    "constraint_changed": "A constraint changed. Review indexes, keys, and referential integrity.",
}


def _parse_file(path: Path) -> Schema:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".sql":
        return parse_sql(text, name=path.stem)
    if suffix == ".json":
        return parse_json_schema(json.loads(text), name=path.stem)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError("PyYAML is required to parse YAML files") from exc
        return parse_json_schema(yaml.safe_load(text), name=path.stem)
    raise ValueError(f"unsupported schema file: {path}")


def _collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES:
                    files.append(child)
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(path)
    return files


def _merge_schemas(schemas: list[Schema]) -> Schema:
    merged = Schema(name="schema", tables={})
    for schema in schemas:
        for table_name, table in schema.tables.items():
            if table_name not in merged.tables:
                merged.tables[table_name] = table
                continue
            existing = merged.tables[table_name]
            names = {field.name for field in existing.fields}
            for field in table.fields:
                if field.name not in names:
                    existing.fields.append(field)
                    names.add(field.name)
            existing.indexes.extend(item for item in table.indexes if item not in existing.indexes)
            existing.foreign_keys.extend(item for item in table.foreign_keys if item not in existing.foreign_keys)
    return merged


def _load_schema(paths: list[Path] | list[str]) -> Schema:
    path_objects = [Path(item) for item in paths]
    files = _collect_files(path_objects)
    if not files:
        raise ValueError("no schema files found")
    return _merge_schemas([_parse_file(path) for path in files])


def _severity_to_sarif(severity: str) -> str:
    return {"error": "error", "warning": "warning", "info": "note"}.get(severity, "warning")


def _format_result(result: Any, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(result.to_dict(), indent=2)
    if output_format == "sarif":
        sarif = {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [
                {
                    "tool": {"driver": {"name": "schema-drift"}},
                    "results": [
                        {
                            "ruleId": finding.code,
                            "level": _severity_to_sarif(finding.severity),
                            "message": {"text": finding.message},
                            "locations": [
                                {
                                    "physicalLocation": {
                                        "artifactLocation": {"uri": finding.path}
                                    }
                                }
                            ],
                        }
                        for finding in result.findings
                    ],
                }
            ],
        }
        return json.dumps(sarif, indent=2)
    lines: list[str] = []
    for finding in result.findings:
        lines.append(f"[{finding.severity.upper()}] {finding.path}: {finding.message}")
        if finding.remediation:
            lines.append(f"  remediation: {finding.remediation}")
    if not result.findings:
        lines.append("No drift detected.")
    lines.append(f"Summary: {result.counts()}")
    return "\n".join(lines)


def _schema_paths(args: argparse.Namespace, config: Any) -> list[Path]:
    if getattr(args, "paths", None):
        return [Path(item) for item in args.paths]
    return list(config.schema_paths)


def cmd_init(args: argparse.Namespace) -> int:
    from schema_drift.config import DEFAULT_CONFIG

    path = Path(args.config) if args.config else Path(".schemadrift.toml")
    if path.exists() and not args.force:
        print(f"error: {path} already exists; use --force to overwrite", file=sys.stderr)
        return EXIT_ERROR
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    Path(".schemadrift").mkdir(exist_ok=True)
    print(f"Created {path}")
    return EXIT_OK


def cmd_baseline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    paths = _schema_paths(args, config)
    if not paths:
        print("error: no schema paths configured", file=sys.stderr)
        return EXIT_ERROR
    schema = _load_schema(paths)
    output = Path(args.output) if args.output else (config.baseline or Path(".schemadrift/baseline.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema.to_dict(), indent=2), encoding="utf-8")
    print(f"Wrote baseline to {output}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    paths = _schema_paths(args, config)
    if not paths:
        print("error: no schema paths configured", file=sys.stderr)
        return EXIT_ERROR
    current = _load_schema(paths)
    baseline_path = Path(args.baseline) if args.baseline else config.baseline
    if baseline_path is None or not baseline_path.exists():
        print("error: baseline not found; run 'schema-drift baseline' first", file=sys.stderr)
        return EXIT_ERROR
    baseline = Schema.from_dict(json.loads(baseline_path.read_text(encoding="utf-8")))
    mode = args.mode or config.mode
    output_format = args.format or config.output
    result = compare_schemas(
        baseline,
        current,
        aliases=config.aliases,
        ignore=config.ignore,
        mode=mode,
    )
    print(_format_result(result, output_format))
    return EXIT_DRIFT if result.has_errors() else EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    left = args.left or (args.paths[0] if getattr(args, "paths", None) and len(args.paths) > 0 else None)
    right = args.right or (args.paths[1] if getattr(args, "paths", None) and len(args.paths) > 1 else None)
    if not left or not right:
        print("error: diff requires two schema paths", file=sys.stderr)
        return EXIT_ERROR
    left_schema = _load_schema([Path(left)])
    right_schema = _load_schema([Path(right)])
    mode = args.mode or config.mode
    output_format = args.format or config.output
    result = compare_schemas(
        left_schema,
        right_schema,
        aliases=config.aliases,
        ignore=config.ignore,
        mode=mode,
    )
    print(_format_result(result, output_format))
    return EXIT_DRIFT if result.has_errors() else EXIT_OK


def cmd_explain(args: argparse.Namespace) -> int:
    print(EXPLANATIONS.get(args.code, "No explanation available for that code."))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="schema-drift", description="Detect drift between declared schemas and runtime artifacts.")
    parser.add_argument("--config", "-c", help="path to .schemadrift.toml or YAML configuration")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create a starter configuration")
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing configuration")
    init_parser.set_defaults(func=cmd_init)

    baseline_parser = subparsers.add_parser("baseline", help="write the current schema baseline")
    baseline_parser.add_argument("paths", nargs="*", help="schema files or directories")
    baseline_parser.add_argument("--output", "-o", help="baseline output path")
    baseline_parser.set_defaults(func=cmd_baseline)

    check_parser = subparsers.add_parser("check", help="compare current schemas against the baseline")
    check_parser.add_argument("paths", nargs="*", help="schema files or directories")
    check_parser.add_argument("--baseline", help="baseline path")
    check_parser.add_argument("--mode", choices=["strict", "additive"], help="compatibility mode")
    check_parser.add_argument("--format", "-f", choices=["human", "json", "sarif"], help="output format")
    check_parser.set_defaults(func=cmd_check)

    diff_parser = subparsers.add_parser("diff", help="compare two schema snapshots")
    diff_parser.add_argument("paths", nargs="*", help="left and right schema paths")
    diff_parser.add_argument("--from", dest="left", help="left schema path")
    diff_parser.add_argument("--to", dest="right", help="right schema path")
    diff_parser.add_argument("--mode", choices=["strict", "additive"], help="compatibility mode")
    diff_parser.add_argument("--format", "-f", choices=["human", "json", "sarif"], help="output format")
    diff_parser.set_defaults(func=cmd_diff)

    explain_parser = subparsers.add_parser("explain", help="explain a drift finding code")
    explain_parser.add_argument("code", help="finding code")
    explain_parser.set_defaults(func=cmd_explain)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_ERROR
    try:
        return int(args.func(args))
    except (ConfigError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
