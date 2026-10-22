"""JSON Schema and OpenAPI parser for schema-drift."""

from __future__ import annotations

import json
from typing import Any

from schema_drift.models import Field, Schema, Table


class JsonSchemaParser:
    """Parse JSON Schema and OpenAPI documents into the normalized model."""

    def parse(self, document: Any, name: str = "schema") -> Schema:
        schema = Schema(name=name, tables={})
        if not isinstance(document, dict):
            return schema
        components = document.get("components", {}).get("schemas", {})
        definitions = document.get("definitions") or document.get("$defs") or {}
        for table_name, subschema in {**definitions, **components}.items():
            self._add_table(schema, str(table_name), subschema)
        if "properties" in document or "type" in document or "allOf" in document:
            table_name = document.get("title") or name or "root"
            self._add_table(schema, str(table_name), document)
        if not schema.tables:
            for key, value in document.items():
                if isinstance(value, dict) and ("properties" in value or "type" in value):
                    self._add_table(schema, str(key), value)
        self._parse_paths(schema, document)
        return schema

    def _merge(self, subschema: dict[str, Any]) -> dict[str, Any]:
        merged = dict(subschema)
        for key in ("allOf", "anyOf", "oneOf"):
            for part in subschema.get(key, []):
                if not isinstance(part, dict):
                    continue
                for item_key, item_value in part.items():
                    if item_key == "properties":
                        merged.setdefault("properties", {}).update(item_value or {})
                    elif item_key == "required":
                        merged.setdefault("required", [])
                        merged["required"].extend(item_value or [])
                    else:
                        merged.setdefault(item_key, item_value)
        return merged

    def _add_table(self, schema: Schema, name: str, subschema: Any) -> None:
        if not isinstance(subschema, dict):
            return
        merged = self._merge(subschema)
        properties = merged.get("properties") or {}
        required = set(merged.get("required") or [])
        fields: list[Field] = []
        for property_name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                property_schema = {}
            property_schema = self._merge(property_schema)
            fields.append(
                Field(
                    name=str(property_name),
                    type=self._normalize_type(property_schema),
                    nullable=str(property_name) not in required,
                    default=self._default(property_schema),
                    metadata={"schema": property_schema},
                )
            )
        if name not in schema.tables:
            schema.tables[name] = Table(name=name, fields=fields)
        else:
            existing = schema.tables[name]
            names = {field.name for field in existing.fields}
            for field in fields:
                if field.name not in names:
                    existing.fields.append(field)
                    names.add(field.name)

    def _normalize_type(self, subschema: dict[str, Any]) -> str:
        if "$ref" in subschema:
            ref = str(subschema["$ref"]).rsplit("/", 1)[-1]
            return ref or "reference"
        raw_type = subschema.get("type")
        if isinstance(raw_type, list):
            raw_type = raw_type[0] if raw_type else None
        fmt = subschema.get("format")
        if raw_type == "string":
            if fmt == "date":
                return "date"
            if fmt in {"date-time", "datetime"}:
                return "datetime"
            if fmt == "uuid":
                return "uuid"
            if fmt == "binary":
                return "binary"
            return "string"
        if raw_type == "integer":
            return "integer"
        if raw_type == "number":
            return "decimal" if fmt == "decimal" else "float"
        if raw_type == "boolean":
            return "boolean"
        if raw_type == "array":
            items = subschema.get("items", {})
            item_type = self._normalize_type(items) if isinstance(items, dict) else "unknown"
            return f"array<{item_type}>"
        if raw_type == "object":
            return "object"
        if "enum" in subschema:
            return "enum"
        return "unknown"

    def _default(self, subschema: dict[str, Any]) -> str | None:
        if "default" not in subschema:
            return None
        value = subschema["default"]
        if isinstance(value, str):
            return value
        return json.dumps(value)

    def _parse_paths(self, schema: Schema, document: dict[str, Any]) -> None:
        paths = document.get("paths")
        if not isinstance(paths, dict):
            return
        methods = {"get", "post", "put", "patch", "delete"}
        for path, operations in paths.items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                if method.lower() not in methods or not isinstance(operation, dict):
                    continue
                operation_id = operation.get("operationId") or f"{method}_{path}".replace("/", "_")
                request_body = operation.get("requestBody", {})
                if isinstance(request_body, dict):
                    content = request_body.get("content", {})
                    if isinstance(content, dict):
                        for media, info in content.items():
                            if isinstance(info, dict) and isinstance(info.get("schema"), dict):
                                self._add_table(schema, f"{operation_id}_request", info["schema"])
                responses = operation.get("responses", {})
                if isinstance(responses, dict):
                    for status, response in responses.items():
                        if not isinstance(response, dict):
                            continue
                        content = response.get("content", {})
                        if not isinstance(content, dict):
                            continue
                        for media, info in content.items():
                            if isinstance(info, dict) and isinstance(info.get("schema"), dict):
                                self._add_table(schema, f"{operation_id}_response_{status}", info["schema"])


def parse_json_schema(document: Any, name: str = "schema") -> Schema:
    """Parse a JSON Schema or OpenAPI document using the default parser."""
    return JsonSchemaParser().parse(document, name=name)
