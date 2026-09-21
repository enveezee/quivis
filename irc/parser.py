"""Pure IRC message parsing and serialization."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


class ParseError(ValueError):
    """Raised when a line cannot be represented as an IRC message."""


_TAG_UNESCAPES = {
    ":": ";",
    "s": " ",
    "r": "\r",
    "n": "\n",
    "\\": "\\",
}
_TAG_ESCAPES = {value: key for key, value in _TAG_UNESCAPES.items()}


def _unescape_tag(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            result.append(_TAG_UNESCAPES.get(value[index + 1], value[index + 1]))
            index += 2
        else:
            result.append(value[index])
            index += 1
    return "".join(result)


def _escape_tag(value: str) -> str:
    return "".join("\\" + _TAG_ESCAPES.get(char, char) for char in value)


def _parse_tags(value: str) -> Mapping[str, str]:
    tags: dict[str, str] = {}
    if not value:
        return MappingProxyType(tags)
    for item in value.split(";"):
        key, separator, tag_value = item.partition("=")
        if not key or any(char in key for char in "\x00\r\n ;"):
            raise ParseError(f"invalid IRC tag key: {key!r}")
        tags[key] = _unescape_tag(tag_value) if separator else ""
    return MappingProxyType(tags)


@dataclass(frozen=True, slots=True)
class IrcMessage:
    """An immutable IRC message with normalized tags and parameters."""

    command: str
    params: tuple[str, ...] = ()
    prefix: str | None = None
    tags: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not self.command or any(char in self.command for char in " \r\n\x00"):
            raise ValueError("command must be a non-empty IRC token")
        object.__setattr__(self, "command", self.command.upper())
        object.__setattr__(self, "params", tuple(self.params))
        object.__setattr__(self, "tags", MappingProxyType(dict(self.tags)))

    @classmethod
    def parse(cls, raw: str | bytes) -> "IrcMessage":
        return parse_message(raw)

    def to_wire(self, *, encoding: str = "utf-8") -> bytes:
        """Serialize this message with IRC CRLF framing."""
        parts: list[str] = []
        if self.tags:
            parts.append("@" + ";".join(
                key + ("=" + _escape_tag(value) if value != "" else "")
                for key, value in self.tags.items()
            ))
        if self.prefix is not None:
            parts.append(":" + self.prefix)
        parts.append(self.command)
        if self.params:
            for index, param in enumerate(self.params):
                is_last = index == len(self.params) - 1
                if is_last and (not param or any(char in param for char in " \r\n") or param.startswith(":") or param == ""):
                    parts.append(":" + param)
                else:
                    parts.append(param)
        return (" ".join(parts) + "\r\n").encode(encoding)


def parse_message(raw: str | bytes) -> IrcMessage:
    """Parse one IRC line; framing CR/LF is accepted but not required."""
    if isinstance(raw, bytes):
        raw = raw.rstrip(b"\r\n").decode("utf-8", errors="replace")
    else:
        raw = raw.rstrip("\r\n")
    if not raw or "\x00" in raw:
        raise ParseError("IRC message is empty or contains NUL")

    tags: Mapping[str, str] = MappingProxyType({})
    prefix: str | None = None
    if raw.startswith("@"):
        tag_text, separator, raw = raw[1:].partition(" ")
        if not separator:
            raise ParseError("tag section has no command")
        tags = _parse_tags(tag_text)
    if raw.startswith(":"):
        prefix_text, separator, raw = raw[1:].partition(" ")
        if not separator or not prefix_text:
            raise ParseError("prefix has no command")
        prefix = prefix_text
    if not raw:
        raise ParseError("missing command")

    command_and_params = raw.split(" ")
    command = command_and_params[0]
    if not command:
        raise ParseError("missing command")
    remainder = raw[len(command):]
    params: list[str] = []
    while remainder:
        remainder = remainder.lstrip(" ")
        if not remainder:
            break
        if remainder.startswith(":"):
            params.append(remainder[1:])
            break
        value, separator, remainder = remainder.partition(" ")
        params.append(value)
        if not separator:
            break
    return IrcMessage(command=command, params=tuple(params), prefix=prefix, tags=tags)