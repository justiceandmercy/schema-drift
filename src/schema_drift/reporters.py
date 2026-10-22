"""Rendering and baseline serialization for schema-drift findings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "Severity",
    "ReportFinding",
    "BaselineSnapshot",
    "render_text",
    "render_json",
    "render_sarif",
    "serialize_baseline",
    "deserialize_baseline",
    "TextReporter",
    "JsonReporter",
    "SarifReporter",
    "BaselineSerializer",
]


class Severity(str, Enum):
    """Severity levels used by schema-drift findings."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @classmethod
    def from_value(cls, value: Any) -> "Severity":
        """Normalize an arbitrary value to a severity level."""
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        if text in {"err", "critical", "fatal"}:
            return cls.ERROR
        if text in {"warn"}:
            return cls.WARNING
        return cls.INFO


_SARIF_LEVELS = {
    Severity.ERROR.value: "error",
    Severity.WARNING.value: "warning",
    Severity.INFO.value: "note",
}

_KNOWN_FINDING_KEYS = {
    "id",
    "rule",
    "rule_id",
    "code",
    "severity",
    "level",
    "message",
    "description",
    "path",
    "location_path",
    "location",
    "remediation",
    "fix",
    "suggestion",
}


@dataclass
class ReportFinding:
    """A normalized finding ready for rendering."""

    id: str = ""
    rule: str = "schema-drift"
    severity: str = Severity.WARNING.value
    message: str = ""
    path: str | None = None
    location: dict[str, Any] | None = None
    remediation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BaselineSnapshot:
    """A serializable schema snapshot used for future comparisons."""

    version: str = "1"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schemas: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def _as_mapping(value: Any) -> dict[str, Any]:
    """Convert a finding or snapshot-like object into a mutable mapping."""
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    result: dict[str, Any] = {}
    for attribute in (
        "id",
        "rule",
        "rule_id",
        "code",
        "severity",
        "level",
        "message",
        "description",
        "path",
        "location_path",
        "location",
        "remediation",
        "fix",
        "suggestion",
    ):
        if hasattr(value, attribute):
            result[attribute] = getattr(value, attribute)
    if not result and hasattr(value, "__dict__"):
        result.update(vars(value))
    return result


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present value for the supplied keys."""
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def normalize_finding(finding: Any) -> ReportFinding:
    """Normalize arbitrary finding objects into :class:`ReportFinding`."""
    data = _as_mapping(finding)
    severity = _first(data, "severity", "level", default=Severity.WARNING.value)
    metadata = {
        key: value for key, value in data.items() if key not in _KNOWN_FINDING_KEYS
    }
    return ReportFinding(
        id=str(_first(data, "id", "rule_id", "code", default="") or ""),
        rule=str(
            _first(data, "rule", "rule_id", "code", default="schema-drift") or "schema-drift"
        ),
        severity=Severity.from_value(severity).value,
        message=str(_first(data, "message", "description", default="") or ""),
        path=_first(data, "path", "location_path"),
        location=_first(data, "location"),
        remediation=_first(data, "remediation", "fix", "suggestion"),
        metadata=metadata,
    )


def _severity_rank(finding: ReportFinding) -> int:
    return {"error": 0, "warning": 1, "info": 2}.get(finding.severity, 3)


def _counts(findings: Sequence[ReportFinding]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def render_text(
    findings: Iterable[Any],
    *,
    title: str = "schema-drift findings",
    include_remediation: bool = True,
) -> str:
    """Render findings as a human-readable report."""
    normalized = [normalize_finding(item) for item in (findings or [])]
    normalized.sort(key=lambda item: (_severity_rank(item), item.path or "", item.message))
    counts = _counts(normalized)
    lines = [title, "=" * len(title), ""]
    if not normalized:
        lines.append("No findings.")
    else:
        for finding in normalized:
            location = f" ({finding.path})" if finding.path else ""
            lines.append(
                f"[{finding.severity.upper()}] {finding.rule}: {finding.message}{location}"
            )
            if finding.location:
                lines.append(f"  location: {finding.location}")
            if include_remediation and finding.remediation:
                lines.append(f"  remediation: {finding.remediation}")
    lines.append("")
    lines.append(
        "Summary: "
        f"{counts.get('error', 0)} error(s), "
        f"{counts.get('warning', 0)} warning(s), "
        f"{counts.get('info', 0)} info item(s)."
    )
    return "\n".join(lines)


def render_json(findings: Iterable[Any], *, indent: int = 2) -> str:
    """Render findings as a JSON document."""
    normalized = [normalize_finding(item) for item in (findings or [])]
    payload = {
        "findings": [asdict(item) for item in normalized],
        "summary": _counts(normalized),
    }
    return json.dumps(payload, indent=indent, sort_keys=True, default=str)


def render_sarif(
    findings: Iterable[Any],
    *,
    tool_name: str = "schema-drift",
    tool_version: str = "0.1.0",
) -> str:
    """Render findings as a SARIF 2.1.0 document."""
    normalized = [normalize_finding(item) for item in (findings or [])]
    results: list[dict[str, Any]] = []
    for finding in normalized:
        result: dict[str, Any] = {
            "ruleId": finding.rule or finding.id or "schema-drift",
            "level": _SARIF_LEVELS.get(finding.severity, "warning"),
            "message": {"text": finding.message},
        }
        locations: list[dict[str, Any]] = []
        if finding.location:
            artifact_uri = finding.location.get("uri") or finding.location.get("file")
            region: dict[str, Any] = {}
            if "line" in finding.location:
                region["startLine"] = finding.location["line"]
            if "column" in finding.location:
                region["startColumn"] = finding.location["column"]
            locations.append(
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": artifact_uri or finding.path or ""},
                        "region": region,
                    }
                }
            )
        elif finding.path:
            locations.append(
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding.path},
                        "region": {},
                    }
                }
            )
        if locations:
            result["locations"] = locations
        if finding.remediation:
            result["fixes"] = [
                {"description": {"text": finding.remediation}}
            ]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": tool_name,
                        "version": tool_version,
                        "informationUri": "https://github.com/schema-drift/schema-drift",
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2, default=str)


def serialize_baseline(
    snapshot: Any, *, path: str | Path | None = None, indent: int = 2
) -> str:
    """Serialize a baseline snapshot to JSON, optionally writing it to ``path``."""
    if isinstance(snapshot, BaselineSnapshot):
        data = asdict(snapshot)
    else:
        data = _as_mapping(snapshot)
    text = json.dumps(data, indent=indent, sort_keys=True, default=str) + "\n"
    if path is not None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
    return text


def deserialize_baseline(source: str | Path) -> BaselineSnapshot | dict[str, Any]:
    """Load a baseline snapshot from a path or JSON string."""
    candidate = Path(source)
    if candidate.exists():
        text = candidate.read_text(encoding="utf-8")
    else:
        text = str(source)
    data = json.loads(text)
    if isinstance(data, Mapping) and "schemas" in data:
        return BaselineSnapshot(
            version=str(data.get("version", "1")),
            created_at=str(
                data.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
            schemas=dict(data.get("schemas") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
    return data


class TextReporter:
    """Reporter that renders findings as text."""

    def render(self, findings: Iterable[Any], **kwargs: Any) -> str:
        return render_text(findings, **kwargs)

    def write(self, findings: Iterable[Any], destination: str | Path, **kwargs: Any) -> None:
        Path(destination).write_text(self.render(findings, **kwargs) + "\n", encoding="utf-8")


class JsonReporter:
    """Reporter that renders findings as JSON."""

    def render(self, findings: Iterable[Any], **kwargs: Any) -> str:
        return render_json(findings, **kwargs)

    def write(self, findings: Iterable[Any], destination: str | Path, **kwargs: Any) -> None:
        Path(destination).write_text(self.render(findings, **kwargs) + "\n", encoding="utf-8")


class SarifReporter:
    """Reporter that renders findings as SARIF."""

    def render(self, findings: Iterable[Any], **kwargs: Any) -> str:
        return render_sarif(findings, **kwargs)

    def write(self, findings: Iterable[Any], destination: str | Path, **kwargs: Any) -> None:
        Path(destination).write_text(self.render(findings, **kwargs) + "\n", encoding="utf-8")


class BaselineSerializer:
    """Serializer for baseline snapshots."""

    def dump(self, snapshot: Any, destination: str | Path | None = None, **kwargs: Any) -> str:
        return serialize_baseline(snapshot, path=destination, **kwargs)

    def load(self, source: str | Path) -> BaselineSnapshot | dict[str, Any]:
        return deserialize_baseline(source)


# Compatibility aliases for callers that expect alternative names.
render_findings_text = render_text
render_findings_json = render_json
render_findings_sarif = render_sarif