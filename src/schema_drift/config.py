"""Configuration loading and validation for schema-drift."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or validated."""


@dataclass
class Config:
    root: Path
    schema_paths: list[Path] = field(default_factory=list)
    baseline: Path | None = None
    ignore: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    mode: str = "strict"
    output: str = "human"
    cache: bool = True
    config_path: Path | None = None


DEFAULT_CONFIG = '''# schema-drift configuration
schemas = ["schemas"]
baseline = ".schemadrift/baseline.json"
mode = "strict"
format = "human"
ignore = []
aliases = {}
'''

CONFIG_NAMES = (
    ".schemadrift.toml",
    "schemadrift.toml",
    ".schemadrift.yaml",
    ".schemadrift.yml",
    "schemadrift.yaml",
    "schemadrift.yml",
)


def find_config(start: Path | str | None = None) -> Path | None:
    """Find the nearest schema-drift configuration file."""
    current = Path(start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        for name in CONFIG_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def _read_raw(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".toml":
        with path.open("rb") as handle:
            return tomllib.load(handle)
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise ConfigError("PyYAML is required to read YAML configuration") from exc
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ConfigError("configuration must contain a mapping")
        return data
    raise ConfigError(f"unsupported configuration format: {path}")


def _section(raw: dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw.get("tool"), dict) and isinstance(raw["tool"].get("schema-drift"), dict):
        return raw["tool"]["schema-drift"]
    if isinstance(raw.get("schema-drift"), dict):
        return raw["schema-drift"]
    return raw


def _resolve(root: Path, value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _as_paths(root: Path, value: Any) -> list[Path]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        raise ConfigError("schema paths must be a string or list of strings")
    result: list[Path] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError("schema paths must be strings")
        result.append(_resolve(root, item) or root)
    return result


def load_config(path: str | Path | None = None) -> Config:
    """Load and validate a schema-drift configuration file."""
    config_path = Path(path).resolve() if path else find_config()
    if config_path is None:
        return Config(root=Path.cwd().resolve(), config_path=None)
    raw = _read_raw(config_path)
    section = _section(raw)
    root = _resolve(config_path.parent, section.get("root", ".")) or config_path.parent
    assert root is not None
    mode = str(section.get("mode", "strict"))
    if mode not in {"strict", "additive"}:
        raise ConfigError(f"invalid compatibility mode: {mode}")
    output = str(section.get("format", section.get("output", "human")))
    if output not in {"human", "json", "sarif"}:
        raise ConfigError(f"invalid output format: {output}")
    ignore = section.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(item, str) for item in ignore):
        raise ConfigError("ignore rules must be a list of strings")
    aliases = section.get("aliases", {})
    if not isinstance(aliases, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in aliases.items()):
        raise ConfigError("aliases must be a mapping of strings")
    schema_paths = _as_paths(root, section.get("schemas", section.get("schema_paths")))
    baseline = _resolve(root, section.get("baseline", section.get("baseline_path")))
    cache = bool(section.get("cache", True))
    return Config(
        root=root,
        schema_paths=schema_paths,
        baseline=baseline,
        ignore=ignore,
        aliases=aliases,
        mode=mode,
        output=output,
        cache=cache,
        config_path=config_path,
    )
