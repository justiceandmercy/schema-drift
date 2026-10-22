"""Normalized schema models and comparison helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from fnmatch import fnmatch
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ChangeType(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    RENAMED = "renamed"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"
    DEFAULT_CHANGED = "default_changed"
    CONSTRAINT_CHANGED = "constraint_changed"


@dataclass
class Field:
    name: str
    type: str = "unknown"
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False
    unique: bool = False
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Table:
    name: str
    fields: List[Field] = field(default_factory=list)
    indexes: List[Dict[str, Any]] = field(default_factory=list)
    foreign_keys: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def field_map(self) -> Dict[str, Field]:
        return {field.name: field for field in self.fields}


@dataclass
class Schema:
    name: str = "schema"
    tables: Dict[str, Table] = field(default_factory=dict)
    version: Optional[str] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "source": self.source,
            "metadata": self.metadata,
            "tables": {
                name: {
                    "name": table.name,
                    "fields": [asdict(field) for field in table.fields],
                    "indexes": table.indexes,
                    "foreign_keys": table.foreign_keys,
                    "metadata": table.metadata,
                }
                for name, table in self.tables.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Schema":
        tables: Dict[str, Table] = {}
        for name, raw in (data.get("tables") or {}).items():
            fields = []
            for item in raw.get("fields", []):
                allowed = set(Field.__dataclass_fields__)
                fields.append(Field(**{key: value for key, value in item.items() if key in allowed}))
            tables[name] = Table(
                name=raw.get("name", name),
                fields=fields,
                indexes=raw.get("indexes", []) or [],
                foreign_keys=raw.get("foreign_keys", []) or [],
                metadata=raw.get("metadata", {}) or {},
            )
        return cls(
            name=data.get("name", "schema"),
            tables=tables,
            version=data.get("version"),
            source=data.get("source"),
            metadata=data.get("metadata", {}) or {},
        )


@dataclass
class Finding:
    severity: str
    change_type: str
    path: str
    message: str
    remediation: str = ""
    old_value: Any = None
    new_value: Any = None
    code: str = ""


@dataclass
class ComparisonResult:
    left: str
    right: str
    findings: List[Finding] = field(default_factory=list)

    def has_errors(self) -> bool:
        return any(finding.severity == Severity.ERROR for finding in self.findings)

    def counts(self) -> Dict[str, int]:
        counts = {severity.value: 0 for severity in Severity}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        return counts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left": self.left,
            "right": self.right,
            "findings": [asdict(finding) for finding in self.findings],
            "summary": self.counts(),
        }


def _ignored(finding: Finding, ignore: List[str]) -> bool:
    for pattern in ignore:
        if fnmatch(finding.path, pattern) or fnmatch(finding.change_type, pattern):
            return True
    return False


def _severity(change: ChangeType, mode: str, default: Severity) -> Severity:
    if mode == "additive":
        if change == ChangeType.ADDED:
            return Severity.INFO
        if change in {ChangeType.REMOVED, ChangeType.TYPE_CHANGED}:
            return Severity.ERROR
    return default


def _add(
    findings: List[Finding],
    ignore: List[str],
    severity: Severity,
    change: ChangeType,
    path: str,
    message: str,
    remediation: str,
    old: Any = None,
    new: Any = None,
) -> None:
    finding = Finding(
        severity=severity.value,
        change_type=change.value,
        path=path,
        message=message,
        remediation=remediation,
        old_value=old,
        new_value=new,
        code=change.value,
    )
    if not _ignored(finding, ignore):
        findings.append(finding)


def _compare_fields(table: str, name: str, old: Field, new: Field, mode: str, findings: List[Finding], ignore: List[str]) -> None:
    path = f"{table}.{name}"
    if old.type != new.type:
        _add(
            findings,
            ignore,
            Severity.ERROR,
            ChangeType.TYPE_CHANGED,
            path,
            f"type changed from {old.type} to {new.type}",
            "Align the types or add a casting migration.",
            old.type,
            new.type,
        )
    if old.nullable != new.nullable:
        severity = Severity.ERROR if old.nullable and not new.nullable else Severity.WARNING
        _add(
            findings,
            ignore,
            severity,
            ChangeType.NULLABILITY_CHANGED,
            path,
            f"nullability changed from {old.nullable} to {new.nullable}",
            "Update application code and backfill existing rows as needed.",
            old.nullable,
            new.nullable,
        )
    if old.default != new.default:
        _add(
            findings,
            ignore,
            Severity.INFO,
            ChangeType.DEFAULT_CHANGED,
            path,
            f"default changed from {old.default!r} to {new.default!r}",
            "Verify application assumptions and migration defaults.",
            old.default,
            new.default,
        )
    if old.primary_key != new.primary_key or old.unique != new.unique:
        _add(
            findings,
            ignore,
            Severity.WARNING,
            ChangeType.CONSTRAINT_CHANGED,
            path,
            "key or uniqueness constraint changed",
            "Review indexes, keys, and referential integrity.",
            {"primary_key": old.primary_key, "unique": old.unique},
            {"primary_key": new.primary_key, "unique": new.unique},
        )


def _compare_tables(table_name: str, left: Table, right: Table, aliases: dict[str, str], mode: str, findings: List[Finding], ignore: List[str]) -> None:
    left_fields = left.field_map()
    right_fields = right.field_map()
    removed = set(left_fields) - set(right_fields)
    added = set(right_fields) - set(left_fields)
    renamed: dict[str, str] = {}
    for old_name in list(removed):
        target = aliases.get(old_name) or aliases.get(f"{table_name}.{old_name}")
        if target in added:
            renamed[old_name] = target
            removed.discard(old_name)
            added.discard(target)
            _add(
                findings,
                ignore,
                _severity(ChangeType.RENAMED, mode, Severity.WARNING),
                ChangeType.RENAMED,
                f"{table_name}.{target}",
                f"field {table_name}.{old_name} was renamed to {target}",
                "Keep a compatibility alias until clients migrate.",
                old_name,
                target,
            )
            _compare_fields(table_name, target, left_fields[old_name], right_fields[target], mode, findings, ignore)
    for name in sorted(removed):
        _add(
            findings,
            ignore,
            Severity.ERROR,
            ChangeType.REMOVED,
            f"{table_name}.{name}",
            f"field {table_name}.{name} was removed",
            "Restore the field or add a migration and update callers.",
            left_fields[name].to_dict() if hasattr(left_fields[name], "to_dict") else None,
            None,
        )
    for name in sorted(added):
        _add(
            findings,
            ignore,
            _severity(ChangeType.ADDED, mode, Severity.WARNING),
            ChangeType.ADDED,
            f"{table_name}.{name}",
            f"field {table_name}.{name} was added",
            "Update consumers and migrations for the new field.",
            None,
            right_fields[name].type,
        )
    for name in sorted(set(left_fields) & set(right_fields)):
        _compare_fields(table_name, name, left_fields[name], right_fields[name], mode, findings, ignore)
    if left.indexes != right.indexes:
        _add(
            findings,
            ignore,
            Severity.WARNING,
            ChangeType.CONSTRAINT_CHANGED,
            f"table:{table_name}",
            "indexes changed",
            "Review index changes for performance and compatibility.",
            left.indexes,
            right.indexes,
        )
    if left.foreign_keys != right.foreign_keys:
        _add(
            findings,
            ignore,
            Severity.WARNING,
            ChangeType.CONSTRAINT_CHANGED,
            f"table:{table_name}",
            "foreign keys changed",
            "Review referential integrity and migration order.",
            left.foreign_keys,
            right.foreign_keys,
        )


def compare_schemas(
    left: Schema,
    right: Schema,
    *,
    aliases: dict[str, str] | None = None,
    ignore: list[str] | None = None,
    mode: str = "strict",
) -> ComparisonResult:
    """Compare two normalized schemas and return actionable findings."""
    aliases = aliases or {}
    ignore = ignore or []
    findings: List[Finding] = []
    left_tables = set(left.tables)
    right_tables = set(right.tables)
    for name in sorted(left_tables - right_tables):
        _add(
            findings,
            ignore,
            Severity.ERROR,
            ChangeType.REMOVED,
            f"table:{name}",
            f"table {name} was removed",
            "Restore the table or add a safe migration.",
        )
    for name in sorted(right_tables - left_tables):
        _add(
            findings,
            ignore,
            _severity(ChangeType.ADDED, mode, Severity.WARNING),
            ChangeType.ADDED,
            f"table:{name}",
            f"table {name} was added",
            "Update consumers and migrations for the new table.",
        )
    for name in sorted(left_tables & right_tables):
        _compare_tables(name, left.tables[name], right.tables[name], aliases, mode, findings, ignore)
    return ComparisonResult(left=left.name, right=right.name, findings=findings)
