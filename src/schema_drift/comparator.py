"""Schema diffing, compatibility checks, rename heuristics, severity."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from .parsers.sql import Schema, Entity, Field, Severity
except Exception:  # pragma: no cover
    from schema_drift.parsers.sql import Schema, Entity, Field, Severity


class CompatibilityMode(str, Enum):
    STRICT = "strict"
    BACKWARD = "backward"      # new schema must accept old data
    FORWARD = "forward"        # old schema must accept new data
    FULL = "full"
    ADDITIVE = "additive"      # only additions allowed


@dataclass
class Change:
    kind: str
    severity: str
    entity: str
    field: Optional[str] = None
    old: Any = None
    new: Any = None
    message: str = ""
    remediation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_breaking(self) -> bool:
        return self.severity == Severity.ERROR


@dataclass
class SchemaDiff:
    old_name: Optional[str] = None
    new_name: Optional[str] = None
    changes: List[Change] = field(default_factory=list)
    compatibility_mode: str = CompatibilityMode.STRICT.value

    def added(self) -> List[Change]:
        return [c for c in self.changes if c.kind.endswith("added")]

    def removed(self) -> List[Change]:
        return [c for c in self.changes if c.kind.endswith("removed")]

    def breaking(self) -> List[Change]:
        return [c for c in self.changes if c.severity == Severity.ERROR]

    def has_errors(self) -> bool:
        return any(c.severity == Severity.ERROR for c in self.changes)

    def summary(self) -> Dict[str, int]:
        out: Dict[str, int] = {"total": len(self.changes)}
        for c in self.changes:
            out[c.severity] = out.get(c.severity, 0) + 1
            out[c.kind] = out.get(c.kind, 0) + 1
        return out


# Type compatibility: is new_type acceptable where old_type was expected
_WIDENING = {
    ("integer", "bigint"), ("integer", "decimal"), ("integer", "number"),
    ("smallint", "integer"), ("smallint", "bigint"), ("float", "double"),
    ("float", "decimal"), ("varchar", "text"), ("char", "varchar"),
    ("char", "text"), ("string", "text"),
}


def _type_compatible(old: str, new: str, mode: CompatibilityMode) -> bool:
    if old == new:
        return True
    if (old, new) in _WIDENING:
        return True
    # number family
    if old in {"integer", "smallint", "bigint", "float", "double", "decimal", "number"} \
            and new in {"integer", "smallint", "bigint", "float", "double", "decimal", "number"}:
        # allow widening only if numeric rank increases
        rank = {"smallint": 0, "integer": 1, "float": 2, "bigint": 3, "double": 4, "decimal": 5, "number": 6}
        return rank.get(new, 0) >= rank.get(old, 0)
    if old in {"varchar", "char", "text", "string"} and new in {"varchar", "char", "text", "string"}:
        return True
    return False


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _match_entities(old: Schema, new: Schema, aliases: Optional[Dict[str, str]]) -> Tuple[Dict[str, str], Set[str], Set[str]]:
    aliases = aliases or {}
    # alias mapping old->new
    mapping: Dict[str, str] = {}
    old_names = {e.name for e in old.entities}
    new_names = {e.name for e in new.entities}
    # explicit aliases
    for k, v in aliases.items():
        if k in old_names and v in new_names:
            mapping[k] = v
    remaining_old = old_names - set(mapping)
    remaining_new = new_names - set(mapping.values())
    # exact name match
    for name in list(remaining_old):
        if name in remaining_new:
            mapping[name] = name
            remaining_old.discard(name)
            remaining_new.discard(name)
    # fuzzy match by name similarity + field overlap
    for oname in list(remaining_old):
        best = None
        best_score = 0.0
        oent = old.get_entity(oname)
        ofields = {f.name for f in (oent.fields if oent else [])}
        for nname in remaining_new:
            nent = new.get_entity(nname)
            nfields = {f.name for f in (nent.fields if nent else [])}
            overlap = len(ofields & nfields) / max(len(ofields | nfields), 1)
            score = 0.5 * _name_similarity(oname, nname) + 0.5 * overlap
            if score > best_score and score >= 0.55:
                best_score = score
                best = nname
        if best is not None:
            mapping[oname] = best
            remaining_old.discard(oname)
            remaining_new.discard(best)
    return mapping, remaining_old, remaining_new


def _detect_renames(old_entity: Entity, new_entity: Entity, removed: List[str], added: List[str], aliases: Optional[Dict[str, str]]) -> Tuple[Dict[str, str], Set[str], Set[str]]:
    aliases = aliases or {}
    mapping: Dict[str, str] = {}
    # explicit field aliases: could be passed as entity.field? keep simple: global alias names
    rem = set(removed)
    add = set(added)
    # exact alias
    for k, v in aliases.items():
        if k in rem and v in add:
            mapping[k] = v
            rem.discard(k)
            add.discard(v)
    old_fields = {f.name: f for f in old_entity.fields}
    new_fields = {f.name: f for f in new_entity.fields}
    for ofn in list(rem):
        of = old_fields.get(ofn)
        best = None
        best_score = 0.0
        for nfn in add:
            nf = new_fields.get(nfn)
            if of is None or nf is None:
                continue
            if of.type != nf.type:
                continue
            score = _name_similarity(ofn, nfn)
            # boost token overlap
            if score > best_score and score >= 0.6:
                best_score = score
                best = nfn
        if best is not None:
            mapping[ofn] = best
            rem.discard(ofn)
            add.discard(best)
    return mapping, rem, add


def compare(old: Schema, new: Schema, *, mode: str = CompatibilityMode.STRICT.value,
           aliases: Optional[Dict[str, str]] = None,
           ignore: Optional[Set[str]] = None) -> SchemaDiff:
    """Compare two schemas and return a :class:`SchemaDiff`."""

    try:
        mode_enum = CompatibilityMode(mode)
    except ValueError:
        mode_enum = CompatibilityMode.STRICT
    ignore = ignore or set()
    diff = SchemaDiff(old_name=old.name, new_name=new.name,
                      compatibility_mode=mode_enum.value)

    def ignored(entity: str, field: Optional[str] = None) -> bool:
        key = f"{entity}.{field}" if field else entity
        return key in ignore or entity in ignore

    ent_mapping, removed_ents, added_ents = _match_entities(old, new, aliases)

    for ename in sorted(removed_ents):
        if ignored(ename):
            continue
        diff.changes.append(Change(
            kind="entity_removed", severity=Severity.ERROR, entity=ename,
            old=ename, new=None,
            message=f"Entity '{ename}' was removed.",
            remediation="Restore the entity or provide a migration removing it."))
    for ename in sorted(added_ents):
        if ignored(ename):
            continue
        sev = Severity.INFO if mode_enum in (CompatibilityMode.BACKWARD, CompatibilityMode.ADDITIVE, CompatibilityMode.FULL) else Severity.WARNING
        diff.changes.append(Change(
            kind="entity_added", severity=sev, entity=ename,
            old=None, new=ename,
            message=f"Entity '{ename}' was added.",
            remediation="Ensure downstream consumers handle the new entity."))

    for old_name, new_name in ent_mapping.items():
        if ignored(old_name):
            continue
        old_e = old.get_entity(old_name)
        new_e = new.get_entity(new_name)
        if old_e is None or new_e is None:
            continue
        entity_label = new_name if new_name != old_name else old_name
        # rename entity?
        if old_name != new_name:
            diff.changes.append(Change(
                kind="entity_renamed", severity=Severity.WARNING, entity=entity_label,
                old=old_name, new=new_name,
                message=f"Entity renamed from '{old_name}' to '{new_name}'.",
                remediation="Update references or add an alias."))
        old_fields = {f.name for f in old_e.fields}
        new_fields = {f.name for f in new_e.fields}
        removed_fields = old_fields - new_fields
        added_fields = new_fields - old_fields
        rename_map, removed_fields, added_fields = _detect_renames(
            old_e, new_e, list(removed_fields), list(added_fields), aliases)
        for ofn, nfn in rename_map.items():
            of = old_e.get_field(ofn)
            nf = new_e.get_field(nfn)
            sev = Severity.WARNING
            type_changed = of is not None and nf is not None and of.type != nf.type
            if type_changed:
                sev = Severity.ERROR
            diff.changes.append(Change(
                kind="field_renamed", severity=sev, entity=entity_label,
                field=nfn, old=ofn, new=nfn,
                message=f"Field '{ofn}' renamed to '{nfn}'"
                        + (f" with type change {of.type}->{nf.type}" if type_changed else "") + ".",
                remediation="Update consumers; consider keeping old field as deprecated."))
            # type check on renamed
            if type_changed and of is not None and nf is not None:
                compat = _type_compatible(of.type, nf.type, mode_enum)
                if not compat:
                    diff.changes.append(Change(
                        kind="type_changed", severity=Severity.ERROR, entity=entity_label,
                        field=nfn, old=of.type, new=nf.type,
                        message=f"Type changed for '{nfn}': {of.type} -> {nf.type} (incompatible).",
                        remediation="Provide migration or widen type."))
        for fn in sorted(removed_fields):
            if ignored(entity_label, fn):
                continue
            # if mode additive, removal is error (already)
            diff.changes.append(Change(
                kind="field_removed", severity=Severity.ERROR, entity=entity_label,
                field=fn, old=fn, new=None,
                message=f"Field '{fn}' was removed.",
                remediation="Add a migration or keep field for compatibility."))
        for fn in sorted(added_fields):
            if ignored(entity_label, fn):
                continue
            sev = Severity.INFO
            if mode_enum == CompatibilityMode.FORWARD:
                sev = Severity.WARNING
            diff.changes.append(Change(
                kind="field_added", severity=sev, entity=entity_label,
                field=fn, old=None, new=fn,
                message=f"Field '{fn}' was added.",
                remediation="Ensure defaults/backfills are provided."))
        # fields present in both
        common = (old_fields & new_fields) - set(rename_map.keys())  # old names; new same
        # Actually common names identical
        common = old_fields & new_fields
        for fn in sorted(common):
            if ignored(entity_label, fn):
                continue
            of = old_e.get_field(fn)
            nf = new_e.get_field(fn)
            if of is None or nf is None:
                continue
            # type
            if of.type != nf.type:
                compat = _type_compatible(of.type, nf.type, mode_enum)
                sev = Severity.ERROR if not compat else Severity.WARNING
                # additive mode: type change always error
                if mode_enum == CompatibilityMode.ADDITIVE:
                    sev = Severity.ERROR
                diff.changes.append(Change(
                    kind="type_changed", severity=sev, entity=entity_label,
                    field=fn, old=of.type, new=nf.type,
                    message=f"Type changed for '{fn}': {of.type} -> {nf.type}.",
                    remediation="Widen type or migrate data."))
            # nullability
            if of.nullable != nf.nullable:
                # tightening nullable->not nullable is breaking
                if of.nullable and not nf.nullable:
                    sev = Severity.ERROR
                else:
                    sev = Severity.INFO
                diff.changes.append(Change(
                    kind="nullability_changed", severity=sev, entity=entity_label,
                    field=fn, old=of.nullable, new=nf.nullable,
                    message=f"Nullability changed for '{fn}': {of.nullable} -> {nf.nullable}.",
                    remediation="Check data and constraints."))
            # default
            if of.has_default != nf.has_default or (of.has_default and of.default != nf.default):
                diff.changes.append(Change(
                    kind="default_changed", severity=Severity.WARNING, entity=entity_label,
                    field=fn, old=of.default, new=nf.default,
                    message=f"Default changed for '{fn}'.",
                    remediation="Verify backfill/default behavior."))
        # indexes
        old_idx = {(tuple(i.columns), i.unique) for i in old_e.indexes}
        new_idx = {(tuple(i.columns), i.unique) for i in new_e.indexes}
        for cols, uniq in old_idx - new_idx:
            diff.changes.append(Change(
                kind="index_removed", severity=Severity.WARNING, entity=entity_label,
                field=",".join(cols), old=cols, new=None,
                message=f"Index on {cols} removed.",
                remediation="Re-add index or update query plans."))
        for cols, uniq in new_idx - old_idx:
            diff.changes.append(Change(
                kind="index_added", severity=Severity.INFO, entity=entity_label,
                field=",".join(cols), old=None, new=cols,
                message=f"Index on {cols} added.",
                remediation="Ensure index creation is performant."))
        # foreign keys
        old_fk = {(tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns)) for fk in old_e.foreign_keys}
        new_fk = {(tuple(fk.columns), fk.ref_table, tuple(fk.ref_columns)) for fk in new_e.foreign_keys}
        for fk in old_fk - new_fk:
            diff.changes.append(Change(
                kind="foreign_key_removed", severity=Severity.WARNING, entity=entity_label,
                field=",".join(fk[0]), old=fk, new=None,
                message=f"Foreign key {fk} removed.",
                remediation="Verify referential integrity."))
        for fk in new_fk - old_fk:
            diff.changes.append(Change(
                kind="foreign_key_added", severity=Severity.INFO, entity=entity_label,
                field=",".join(fk[0]), old=None, new=fk,
                message=f"Foreign key {fk} added.",
                remediation="Ensure referenced data exists."))

    # additive mode: promote any removal/type-change? already errors; also flag added as ok.
    if mode_enum == CompatibilityMode.ADDITIVE:
        for c in diff.changes:
            if c.kind in ("field_removed", "entity_removed", "type_changed"):
                c.severity = Severity.ERROR
    return diff


def check_compatibility(diff: SchemaDiff, mode: Optional[str] = None) -> Tuple[bool, List[Change]]:
    """Return (compatible, breaking_changes) for a diff."""

    mode = mode or diff.compatibility_mode
    breaking = diff.breaking()
    if mode in (CompatibilityMode.BACKWARD.value, CompatibilityMode.ADDITIVE.value):
        # removals and incompatible type changes break
        pass
    return (len(breaking) == 0, breaking)


def diff(old: Schema, new: Schema, **kw: Any) -> SchemaDiff:
    return compare(old, new, **kw)


def diff_schemas(old: Schema, new: Schema, **kw: Any) -> SchemaDiff:
    return compare(old, new, **kw)


def compare_schemas(old: Schema, new: Schema, **kw: Any) -> SchemaDiff:
    return compare(old, new, **kw)


__all__ = ["CompatibilityMode", "Change", "SchemaDiff", "compare", "diff",
           "diff_schemas", "compare_schemas", "check_compatibility"]
