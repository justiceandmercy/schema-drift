"""Built-in validation rules for naming, nullability, defaults, indexes, FKs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

try:
    from .parsers.sql import Schema, Entity, Field, Severity
except Exception:  # pragma: no cover
    from schema_drift.parsers.sql import Schema, Entity, Field, Severity


@dataclass
class Violation:
    rule_id: str
    severity: str
    entity: str
    field: Optional[str] = None
    message: str = ""
    remediation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuleConfig:
    field_name_pattern: str = r"^[a-z][a-z0-9_]*$"
    entity_name_pattern: str = r"^[A-Za-z][A-Za-z0-9_]*$"
    require_snake_case_fields: bool = True
    require_primary_key: bool = True
    max_nullable_without_default: bool = True
    require_index_for_foreign_keys: bool = True
    forbidden_field_names: Set[str] = field(default_factory=lambda: {"data", "info", "value", "temp"})
    allowed_types: Optional[Set[str]] = None
    naming_exceptions: Set[str] = field(default_factory=set)


def _add(violations: List[Violation], **kw: Any) -> None:
    violations.append(Violation(**kw))


def check_naming(schema: Schema, config: RuleConfig, violations: List[Violation]) -> None:
    for entity in schema.entities:
        if not re.match(config.entity_name_pattern, entity.name):
            _add(violations, rule_id="NAMING001", severity=Severity.WARNING,
                 entity=entity.name, message=f"Entity name '{entity.name}' does not match pattern.",
                 remediation="Rename to match naming convention.")
        for f in entity.fields:
            if f.name in config.naming_exceptions:
                continue
            if config.require_snake_case_fields and not re.match(config.field_name_pattern, f.name):
                _add(violations, rule_id="NAMING002", severity=Severity.WARNING,
                     entity=entity.name, field=f.name,
                     message=f"Field '{f.name}' is not snake_case.",
                     remediation="Rename field to snake_case.")
            if f.name.lower() in config.forbidden_field_names:
                _add(violations, rule_id="NAMING003", severity=Severity.INFO,
                     entity=entity.name, field=f.name,
                     message=f"Field '{f.name}' uses a vague name.",
                     remediation="Use a more descriptive name.")
            if config.allowed_types and f.type not in config.allowed_types:
                _add(violations, rule_id="NAMING004", severity=Severity.INFO,
                     entity=entity.name, field=f.name,
                     message=f"Type '{f.type}' not in allowed list.",
                     remediation="Map to an approved type.")


def check_nullability(schema: Schema, config: RuleConfig, violations: List[Violation]) -> None:
    for entity in schema.entities:
        for f in entity.fields:
            if f.primary_key and f.nullable:
                _add(violations, rule_id="NULL001", severity=Severity.ERROR,
                     entity=entity.name, field=f.name,
                     message=f"Primary key field '{f.name}' is nullable.",
                     remediation="Mark primary key as NOT NULL.")
            if config.max_nullable_without_default and f.nullable and not f.has_default:
                # only flag non-PK, non-FK-ish optional fields? keep as info
                if not f.primary_key:
                    _add(violations, rule_id="NULL002", severity=Severity.INFO,
                         entity=entity.name, field=f.name,
                         message=f"Nullable field '{f.name}' has no default.",
                         remediation="Consider adding a default or making NOT NULL.")


def check_defaults(schema: Schema, config: RuleConfig, violations: List[Violation]) -> None:
    for entity in schema.entities:
        for f in entity.fields:
            if f.has_default and f.default is not None:
                # check boolean defaults are valid
                if f.type == "boolean" and str(f.default).lower() not in ("true", "false", "0", "1", "null"):
                    _add(violations, rule_id="DEF001", severity=Severity.WARNING,
                         entity=entity.name, field=f.name,
                         message=f"Suspicious boolean default '{f.default}'.",
                         remediation="Use TRUE/FALSE.")
            if f.primary_key and f.has_default:
                _add(violations, rule_id="DEF002", severity=Severity.INFO,
                     entity=entity.name, field=f.name,
                     message=f"Primary key '{f.name}' has a default.",
                     remediation="Verify default is intended.")


def check_indexes(schema: Schema, config: RuleConfig, violations: List[Violation]) -> None:
    for entity in schema.entities:
        if config.require_primary_key and not entity.primary_key and entity.fields:
            _add(violations, rule_id="IDX001", severity=Severity.WARNING,
                 entity=entity.name, message="Entity has no primary key.",
                 remediation="Add a primary key constraint.")
        # duplicate indexes
        seen: Set[Tuple[str, ...]] = set()
        for idx in entity.indexes:
            key = (tuple(idx.columns), idx.unique)
            if key in seen:
                _add(violations, rule_id="IDX002", severity=Severity.WARNING,
                     entity=entity.name, field=",".join(idx.columns),
                     message=f"Duplicate index on {idx.columns}.",
                     remediation="Remove redundant index.")
            seen.add(key)
        # index columns exist
        field_names = {f.name for f in entity.fields}
        for idx in entity.indexes:
            for c in idx.columns:
                if c not in field_names:
                    _add(violations, rule_id="IDX003", severity=Severity.ERROR,
                         entity=entity.name, field=c,
                         message=f"Index references unknown column '{c}'.",
                         remediation="Fix index column name.")


def check_foreign_keys(schema: Schema, config: RuleConfig, violations: List[Violation]) -> None:
    entity_names = {e.name for e in schema.entities}
    for entity in schema.entities:
        for fk in entity.foreign_keys:
            if fk.ref_table not in entity_names:
                _add(violations, rule_id="FK001", severity=Severity.WARNING,
                     entity=entity.name, field=",".join(fk.columns),
                     message=f"Foreign key references unknown table '{fk.ref_table}'.",
                     remediation="Ensure referenced table exists in schema.")
            for c in fk.columns:
                if c not in {f.name for f in entity.fields}:
                    _add(violations, rule_id="FK002", severity=Severity.ERROR,
                         entity=entity.name, field=c,
                         message=f"Foreign key column '{c}' missing.",
                         remediation="Add the column.")
            if config.require_index_for_foreign_keys:
                cols = tuple(fk.columns)
                has_idx = any(tuple(i.columns) == cols or set(i.columns) >= set(fk.columns) for i in entity.indexes)
                # also PK covers
                if not has_idx and cols not in (tuple(entity.primary_key),):
                    _add(violations, rule_id="FK003", severity=Severity.INFO,
                         entity=entity.name, field=",".join(fk.columns),
                         message=f"No index on foreign key columns {fk.columns}.",
                         remediation="Add an index for join performance.")
            if fk.on_delete and fk.on_delete.upper() not in {"CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"}:
                _add(violations, rule_id="FK004", severity=Severity.WARNING,
                     entity=entity.name, field=",".join(fk.columns),
                     message=f"Unknown ON DELETE action '{fk.on_delete}'.",
                     remediation="Use a standard referential action.")


def run_rules(schema: Schema, config: Optional[RuleConfig] = None) -> List[Violation]:
    """Run all built-in rules and return violations."""

    config = config or RuleConfig()
    violations: List[Violation] = []
    check_naming(schema, config, violations)
    check_nullability(schema, config, violations)
    check_defaults(schema, config, violations)
    check_indexes(schema, config, violations)
    check_foreign_keys(schema, config, violations)
    return violations


def validate(schema: Schema, config: Optional[RuleConfig] = None) -> List[Violation]:
    return run_rules(schema, config)


def check(schema: Schema, config: Optional[RuleConfig] = None) -> List[Violation]:
    return run_rules(schema, config)


def run(schema: Schema, config: Optional[RuleConfig] = None) -> List[Violation]:
    return run_rules(schema, config)


__all__ = ["Violation", "RuleConfig", "run_rules", "validate", "check", "run",
           "check_naming", "check_nullability", "check_defaults", "check_indexes",
           "check_foreign_keys"]
