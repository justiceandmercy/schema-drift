"""SQL DDL parser for schema-drift."""

from __future__ import annotations

import re
from typing import Any, List

from schema_drift.models import Field, Schema, Table


def _ident(value: str) -> str:
    return value.strip().strip('"').strip("`").strip("[]").strip()


def _split_top_level(text: str, separator: str = ",") -> List[str]:
    parts: List[str] = []
    depth = 0
    quote: str | None = None
    current: List[str] = []
    for char in text:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in ("'", '"', "`"):
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            current.append(char)
        elif char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _normalize_sql_type(raw: str) -> str:
    value = re.sub(r"\s+", " ", raw.strip().lower())
    base = re.sub(r"\([^)]*\)", "", value).strip()
    if "bigint" in base:
        return "bigint"
    if "smallint" in base:
        return "smallint"
    if "tinyint" in base:
        return "tinyint"
    if "int" in base:
        return "integer"
    if "bool" in base:
        return "boolean"
    if "text" in base:
        return "text"
    if any(token in base for token in ("varchar", "character varying", "nvarchar", "char", "string")):
        return "string"
    if "timestamp" in base:
        return "timestamp"
    if "datetime" in base:
        return "datetime"
    if "date" in base:
        return "date"
    if "time" in base:
        return "time"
    if "decimal" in base or "numeric" in base:
        return "decimal"
    if "double" in base:
        return "double"
    if "real" in base or "float" in base:
        return "float"
    if "json" in base:
        return "json"
    if "uuid" in base:
        return "uuid"
    if any(token in base for token in ("bytea", "blob", "binary", "varbinary")):
        return "binary"
    if "enum" in base:
        return "enum"
    return value


class SQLParser:
    """Parse a subset of SQL DDL into the normalized schema model."""

    def parse(self, sql: str, name: str = "schema") -> Schema:
        schema = Schema(name=name, tables={})
        for statement in self._split_statements(sql):
            statement = statement.strip()
            if not statement:
                continue
            upper = statement.upper()
            if upper.startswith("CREATE TABLE"):
                self._parse_create_table(statement, schema)
            elif "CREATE" in upper and "INDEX" in upper:
                self._parse_index(statement, schema)
            elif upper.startswith("ALTER TABLE"):
                self._parse_alter(statement, schema)
        return schema

    def _split_statements(self, sql: str) -> List[str]:
        return [part.strip() for part in sql.split(";") if part.strip()]

    def _parse_create_table(self, statement: str, schema: Schema) -> None:
        match = re.match(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<name>[\"`]?[\w.]+[\"`]?)\s*\((?P<body>.*)\)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return
        table_name = _ident(match.group("name"))
        table = schema.tables.setdefault(table_name, Table(name=table_name))
        for part in _split_top_level(match.group("body")):
            upper = part.upper()
            if upper.startswith(("CONSTRAINT", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "KEY", "INDEX")):
                self._parse_table_constraint(part, table)
            else:
                self._parse_column(part, table)

    def _parse_column(self, part: str, table: Table) -> None:
        match = re.match(r'^\s*("?\w+"?|`?\w+`?)\s+(.*)$', part, re.DOTALL)
        if not match:
            return
        name = _ident(match.group(1))
        rest = match.group(2).strip()
        type_match = re.match(
            r"^(.+?)(?=\s+(?:NOT\s+NULL|NULL|DEFAULT|PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK|COLLATE|GENERATED|CONSTRAINT)\b|$)",
            rest,
            re.IGNORECASE | re.DOTALL,
        )
        type_raw = type_match.group(1).strip() if type_match else rest.split()[0]
        rest = rest[len(type_raw):].strip() if type_match else ""
        field = Field(name=name, type=_normalize_sql_type(type_raw), metadata={"raw_type": type_raw})
        if re.search(r"\bNOT\s+NULL\b", rest, re.IGNORECASE):
            field.nullable = False
        elif re.search(r"\bNULL\b", rest, re.IGNORECASE):
            field.nullable = True
        default_match = re.search(
            r"\bDEFAULT\b\s+(.+?)(?=\s+(?:NOT\s+NULL|PRIMARY\s+KEY|UNIQUE|REFERENCES|CHECK|COLLATE|GENERATED|CONSTRAINT)\b|$)",
            rest,
            re.IGNORECASE | re.DOTALL,
        )
        if default_match:
            field.default = default_match.group(1).strip().rstrip(",")
        if re.search(r"\bPRIMARY\s+KEY\b", rest, re.IGNORECASE):
            field.primary_key = True
            field.nullable = False
        if re.search(r"\bUNIQUE\b", rest, re.IGNORECASE):
            field.unique = True
        ref_match = re.search(
            r"\bREFERENCES\s+([\"`]?[\w.]+[\"`]?)\s*\(([^)]+)\)",
            rest,
            re.IGNORECASE,
        )
        if ref_match:
            table.foreign_keys.append(
                {
                    "columns": [name],
                    "ref_table": _ident(ref_match.group(1)),
                    "ref_columns": [_ident(ref_match.group(2))],
                }
            )
        table.fields.append(field)

    def _parse_table_constraint(self, part: str, table: Table) -> None:
        primary = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", part, re.IGNORECASE)
        if primary:
            for column in _split_top_level(primary.group(1)):
                name = _ident(column)
                for field in table.fields:
                    if field.name == name:
                        field.primary_key = True
                        field.nullable = False
        foreign = re.search(
            r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([\"`]?[\w.]+[\"`]?)\s*\(([^)]+)\)",
            part,
            re.IGNORECASE,
        )
        if foreign:
            columns = [_ident(item) for item in _split_top_level(foreign.group(1))]
            ref_columns = [_ident(item) for item in _split_top_level(foreign.group(3))]
            table.foreign_keys.append(
                {
                    "columns": columns,
                    "ref_table": _ident(foreign.group(2)),
                    "ref_columns": ref_columns,
                }
            )
        unique = re.search(r"UNIQUE\s*\(([^)]+)\)", part, re.IGNORECASE)
        if unique:
            for column in _split_top_level(unique.group(1)):
                name = _ident(column)
                for field in table.fields:
                    if field.name == name:
                        field.unique = True

    def _parse_index(self, statement: str, schema: Schema) -> None:
        match = re.match(
            r"CREATE\s+(UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([\"`]?\w+[\"`]?)\s+ON\s+([\"`]?[\w.]+[\"`]?)\s*\(([^)]+)\)",
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return
        table_name = _ident(match.group(3))
        table = schema.tables.get(table_name)
        if table is None:
            return
        table.indexes.append(
            {
                "name": _ident(match.group(2)),
                "columns": [_ident(item) for item in _split_top_level(match.group(4))],
                "unique": bool(match.group(1)),
            }
        )

    def _parse_alter(self, statement: str, schema: Schema) -> None:
        match = re.match(r"ALTER\s+TABLE\s+([\"`]?[\w.]+[\"`]?)\s+(.*)$", statement, re.IGNORECASE | re.DOTALL)
        if not match:
            return
        table_name = _ident(match.group(1))
        table = schema.tables.setdefault(table_name, Table(name=table_name))
        rest = match.group(2).strip()
        foreign = re.search(
            r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([\"`]?[\w.]+[\"`]?)\s*\(([^)]+)\)",
            rest,
            re.IGNORECASE,
        )
        if foreign:
            table.foreign_keys.append(
                {
                    "columns": [_ident(item) for item in _split_top_level(foreign.group(1))],
                    "ref_table": _ident(foreign.group(2)),
                    "ref_columns": [_ident(item) for item in _split_top_level(foreign.group(3))],
                }
            )
        column_match = re.match(r"ADD\s+(?:COLUMN\s+)?(.+)$", rest, re.IGNORECASE | re.DOTALL)
        if column_match and "CONSTRAINT" not in rest.upper() and "FOREIGN" not in rest.upper():
            self._parse_column(column_match.group(1), table)


def parse_sql(sql: str, name: str = "schema") -> Schema:
    """Parse SQL DDL using the default parser."""
    return SQLParser().parse(sql, name=name)
