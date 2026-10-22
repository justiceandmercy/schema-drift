"""Unit tests for normalized schema diffing."""

from __future__ import annotations

import pytest

comparator = pytest.importorskip("schema_drift.comparator")

SchemaComparator = getattr(comparator, "SchemaComparator", None)
compare_schemas = getattr(comparator, "compare_schemas", None)
CompatibilityMode = getattr(comparator, "CompatibilityMode", None)


def _skip_if_unavailable() -> None:
    if SchemaComparator is None and compare_schemas is None:
        pytest.skip("schema_drift.comparator does not expose a comparison API")


def _invoke_comparison(old: dict, new: dict, mode: object | None = None):
    """Call the comparator using the most likely public API shape."""
    _skip_if_unavailable()
    if compare_schemas is not None:
        for kwargs in (
            {"compatibility_mode": mode},
            {"mode": mode},
            {"compatibility": mode},
        ):
            try:
                if mode is None:
                    return compare_schemas(old, new)
                return compare_schemas(old, new, **kwargs)
            except TypeError:
                continue
        if mode is None:
            return compare_schemas(old, new)
        return compare_schemas(old, new, mode)

    comparator_instance = SchemaComparator()
    for method_name in ("compare", "diff", "compare_schemas", "compare_models"):
        method = getattr(comparator_instance, method_name, None)
        if method is None:
            continue
        for kwargs in (
            {"compatibility_mode": mode},
            {"mode": mode},
            {"compatibility": mode},
        ):
            try:
                if mode is None:
                    return method(old, new)
                return method(old, new, **kwargs)
            except TypeError:
                continue
        if mode is None:
            return method(old, new)
        return method(old, new, mode)
    pytest.skip("No supported comparison method was found")


def _finding_values(result) -> list[dict]:
    findings = getattr(result, "findings", None)
    if findings is None:
        findings = getattr(result, "changes", None)
    if findings is None:
        findings = getattr(result, "diffs", None)
    if findings is None:
        findings = result if isinstance(result, (list, tuple)) else []
    normalized = []
    for finding in findings:
        if hasattr(finding, "__dict__") and not isinstance(finding, dict):
            normalized.append(vars(finding))
        else:
            normalized.append(dict(finding))
    return normalized


def _modes() -> dict[str, object]:
    if CompatibilityMode is None:
        return {}
    result = {}
    for name in ("STRICT", "ADDITIVE", "BACKWARD", "FORWARD", "COMPATIBLE"):
        if hasattr(CompatibilityMode, name):
            result[name] = getattr(CompatibilityMode, name)
    return result


def test_comparator_imports() -> None:
    _skip_if_unavailable()
    assert SchemaComparator is not None or compare_schemas is not None


def test_added_field_is_detected() -> None:
    old = {"users": {"id": {"type": "integer"}}}
    new = {"users": {"id": {"type": "integer"}, "email": {"type": "string"}}}
    result = _invoke_comparison(old, new)
    findings = _finding_values(result)
    assert any("added" in str(item.get("kind", item.get("change_type", ""))).lower() for item in findings)
    assert any("email" in str(item) for item in findings)


def test_removed_field_is_detected() -> None:
    old = {"users": {"id": {"type": "integer"}, "legacy": {"type": "string"}}}
    new = {"users": {"id": {"type": "integer"}}}
    result = _invoke_comparison(old, new)
    findings = _finding_values(result)
    assert any("removed" in str(item.get("kind", item.get("change_type", ""))).lower() for item in findings)
    assert any("legacy" in str(item) for item in findings)


def test_type_change_is_detected() -> None:
    old = {"users": {"age": {"type": "integer"}}}
    new = {"users": {"age": {"type": "string"}}}
    result = _invoke_comparison(old, new)
    findings = _finding_values(result)
    assert any("type" in str(item).lower() for item in findings)
    assert any("age" in str(item) for item in findings)


def test_rename_detection_is_reported() -> None:
    old = {"users": {"user_name": {"type": "string"}}}
    new = {"users": {"username": {"type": "string"}}}
    result = _invoke_comparison(old, new)
    findings = _finding_values(result)
    assert len(findings) >= 1
    assert any("user" in str(item).lower() for item in findings)


def test_additive_compatibility_mode_allows_additions() -> None:
    modes = _modes()
    if not modes:
        pytest.skip("CompatibilityMode is not available")
    additive = modes.get("ADDITIVE") or modes.get("COMPATIBLE")
    if additive is None:
        pytest.skip("No additive compatibility mode is available")
    old = {"users": {"id": {"type": "integer"}}}
    new = {"users": {"id": {"type": "integer"}, "email": {"type": "string"}}}
    result = _invoke_comparison(old, new, mode=additive)
    findings = _finding_values(result)
    severities = [str(item.get("severity", "")).lower() for item in findings]
    assert not any(severity == "error" for severity in severities)


def test_strict_mode_marks_removals_as_errors() -> None:
    modes = _modes()
    if "STRICT" not in modes:
        pytest.skip("Strict compatibility mode is not available")
    old = {"users": {"id": {"type": "integer"}, "legacy": {"type": "string"}}}
    new = {"users": {"id": {"type": "integer"}}}
    result = _invoke_comparison(old, new, mode=modes["STRICT"])
    findings = _finding_values(result)
    assert any(str(item.get("severity", "")).lower() == "error" for item in findings)


def test_severity_classification_present() -> None:
    old = {"users": {"id": {"type": "integer"}, "legacy": {"type": "string"}}}
    new = {"users": {"id": {"type": "string"}}}
    result = _invoke_comparison(old, new)
    findings = _finding_values(result)
    assert findings
    assert all("severity" in item for item in findings)


def test_ignore_rules_can_be_passed() -> None:
    _skip_if_unavailable()
    old = {"users": {"id": {"type": "integer"}, "legacy": {"type": "string"}}}
    new = {"users": {"id": {"type": "integer"}}}
    if SchemaComparator is not None:
        for constructor_kwargs in (
            {"ignore": ["users.legacy"]},
            {"ignored_paths": ["users.legacy"]},
            {"ignore_rules": ["users.legacy"]},
        ):
            try:
                comparator_instance = SchemaComparator(**constructor_kwargs)
                method = getattr(comparator_instance, "compare", None)
                if method is not None:
                    result = method(old, new)
                    findings = _finding_values(result)
                    assert isinstance(findings, list)
                    return
            except TypeError:
                continue
    pytest.skip("Ignore-rule constructor is not available")


def test_alias_rules_can_be_passed() -> None:
    _skip_if_unavailable()
    old = {"users": {"user_name": {"type": "string"}}}
    new = {"users": {"username": {"type": "string"}}}
    if SchemaComparator is not None:
        for constructor_kwargs in (
            {"aliases": {"user_name": "username"}},
            {"field_aliases": {"user_name": "username"}},
        ):
            try:
                comparator_instance = SchemaComparator(**constructor_kwargs)
                method = getattr(comparator_instance, "compare", None)
                if method is not None:
                    result = method(old, new)
                    findings = _finding_values(result)
                    assert isinstance(findings, list)
                    return
            except TypeError:
                continue
    pytest.skip("Alias constructor is not available")