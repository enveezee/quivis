"""Schema-driven human IRC command parsing and help."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..schema import load


class CommandError(ValueError):
    """Raised when command input or a command schema is invalid."""


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...] = ()
    usage: str = ""
    description: str = ""
    action: str = ""
    args: tuple[str, ...] = ()
    min_args: int = 0
    max_args: int | None = None


@dataclass(frozen=True, slots=True)
class CommandInvocation:
    spec: CommandSpec
    args: tuple[str, ...]
    raw: str


@dataclass(frozen=True, slots=True)
class ParsedInput:
    text: str | None = None
    command: CommandInvocation | None = None


@dataclass(frozen=True, slots=True)
class KeyBinding:
    key: str
    action: str
    argument: str | None = None


class CommandRegistry:
    """Parse user input using command definitions loaded from a schema."""

    def __init__(self, specs: Mapping[str, CommandSpec] | None = None) -> None:
        self._commands: dict[str, CommandSpec] = {}
        self.key_bindings: tuple[KeyBinding, ...] = ()
        for spec in (specs or {}).values():
            self.register(spec)

    @classmethod
    def from_schema(cls, source: Mapping[str, Any] | str | Path) -> "CommandRegistry":
        schema = load(source)
        commands = schema.get("commands", schema)
        if isinstance(commands, Mapping):
            commands = list(commands.values())
        if not isinstance(commands, list):
            raise CommandError("command schema must contain a commands list")
        registry = cls()
        for raw in commands:
            if not isinstance(raw, Mapping) or "name" not in raw:
                raise CommandError("each command needs a name")
            registry.register(CommandSpec(
                name=str(raw["name"]).lower().lstrip("/"),
                aliases=tuple(str(alias).lower().lstrip("/") for alias in raw.get("aliases", [])),
                usage=str(raw.get("usage", "")),
                description=str(raw.get("description", "")),
                action=str(raw.get("action", raw["name"])),
                args=tuple(str(arg) for arg in raw.get("args", [])),
                min_args=int(raw.get("min_args", 0)),
                max_args=int(raw["max_args"]) if raw.get("max_args") is not None else None,
            ))
        registry.key_bindings = tuple(
            KeyBinding(str(raw["key"]), str(raw["action"]), raw.get("argument"))
            for raw in schema.get("key_bindings", [])
            if isinstance(raw, Mapping) and "key" in raw and "action" in raw
        )
        return registry

    def register(self, spec: CommandSpec) -> None:
        names = (spec.name, *spec.aliases)
        for name in names:
            key = name.lower().lstrip("/")
            if not key or key in self._commands:
                raise CommandError(f"duplicate command or alias: {name}")
            self._commands[key] = spec

    def parse(self, text: str) -> ParsedInput:
        if not text.startswith("/"):
            return ParsedInput(text=text)
        try:
            tokens = shlex.split(text[1:])
        except ValueError as exc:
            raise CommandError(f"invalid command quoting: {exc}") from exc
        if not tokens:
            raise CommandError("empty command")
        spec = self._commands.get(tokens[0].lower())
        if spec is None:
            raise CommandError(f"unknown command: /{tokens[0]}")
        args = tuple(tokens[1:])
        if len(args) < spec.min_args:
            raise CommandError(f"usage: /{spec.usage or spec.name}")
        if spec.max_args is not None and len(args) > spec.max_args:
            raise CommandError(f"usage: /{spec.usage or spec.name}")
        return ParsedInput(command=CommandInvocation(spec, args, text))

    def help(self, name: str | None = None) -> str:
        specs = self._unique_specs()
        if name is not None:
            spec = self._commands.get(name.lower().lstrip("/"))
            if spec is None:
                raise CommandError(f"unknown command: /{name}")
            return f"/{spec.usage or spec.name} - {spec.description}".rstrip()
        return "\n".join(
            f"/{spec.usage or spec.name} - {spec.description}".rstrip()
            for spec in sorted(specs, key=lambda item: item.name)
        )

    def _unique_specs(self) -> list[CommandSpec]:
        return list({id(spec): spec for spec in self._commands.values()}.values())
