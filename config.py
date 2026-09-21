"""Platform-aware qirc configuration and server profile persistence."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .schema import SchemaError, load, write


APP_NAME = "qivis"


def config_dir() -> Path:
    override = os.environ.get("QIVIS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(root) / APP_NAME


def profile_path() -> Path:
    return config_dir() / "servers.json"


def load_profiles() -> dict[str, Any]:
    path = profile_path()
    if not path.exists():
        return {"profiles": {}}
    value = load(path)
    if not isinstance(value.get("profiles"), Mapping):
        raise SchemaError("server configuration requires a profiles mapping")
    return value


def save_profiles(value: Mapping[str, Any]) -> Path:
    if "profiles" not in value or not isinstance(value["profiles"], Mapping):
        raise SchemaError("server configuration requires a profiles mapping")
    return write(profile_path(), value)
