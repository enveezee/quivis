"""qivis shared schema I/O and normalization layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class SchemaError(ValueError):
    """Raised when a schema cannot be loaded or written."""


def load(source: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"unable to read schema {path}: {exc}") from exc
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            value = json.loads(text)
        elif suffix == ".toml":
            import tomllib

            value = tomllib.loads(text)
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise SchemaError("YAML support requires optional PyYAML") from exc
            value = yaml.safe_load(text)
        else:
            raise SchemaError(f"unsupported schema format: {suffix!r}")
    except (ValueError, TypeError) as exc:
        raise SchemaError(f"invalid {suffix} schema {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise SchemaError("schema root must be a mapping")
    return dict(value)


def write(destination: str | Path, value: Mapping[str, Any]) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            text = json.dumps(value, indent=2, sort_keys=True) + "\n"
        elif suffix == ".toml":
            try:
                import tomlkit
            except ModuleNotFoundError as exc:
                raise SchemaError("TOML writing requires optional tomlkit") from exc
            text = tomlkit.dumps(dict(value))
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml
            except ModuleNotFoundError as exc:
                raise SchemaError("YAML writing requires optional PyYAML") from exc
            text = yaml.safe_dump(dict(value), sort_keys=False)
        else:
            raise SchemaError(f"unsupported schema format: {suffix!r}")
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"unable to write schema {path}: {exc}") from exc
    return path


def event_bindings(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = schema.get("events", [])
    if isinstance(raw, Mapping):
        raw = [
            {"source": "*", "event": event, "action": action}
            for event, actions in raw.items()
            for action in (actions if isinstance(actions, list) else [actions])
        ]
    if not isinstance(raw, list):
        raise SchemaError("events must be a list or mapping")
    normalized = []
    for binding in raw:
        if not isinstance(binding, Mapping) or not all(
            key in binding for key in ("source", "event", "action")
        ):
            raise SchemaError("each event binding needs source, event, and action")
        normalized.append({
            "source": str(binding["source"]),
            "event": str(binding["event"]),
            "action": str(binding["action"]),
        })
    return normalized
