"""Async stream transport and registration/capability orchestration."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from .parser import IrcMessage, ParseError
from .state import IrcState


class SaslMechanism(Protocol):
    name: str

    def initial_response(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SaslPlain:
    username: str
    password: str
    authorization_id: str = ""
    name: str = "PLAIN"

    def initial_response(self) -> str:
        payload = "\0".join((self.authorization_id, self.username, self.password)).encode()
        return base64.b64encode(payload).decode("ascii")


@dataclass(frozen=True, slots=True)
class ConnectionConfig:
    host: str
    port: int = 6697
    nickname: str = "guest"
    username: str = "guest"
    realname: str = "IRC user"
    password: str | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)
    sasl: SaslMechanism | None = None
    tls: bool = True
    encoding: str = "utf-8"


class IrcConnection:
    """Owns I/O and handshake policy; application traffic remains reactive."""

    def __init__(self, config: ConnectionConfig, state: IrcState | None = None) -> None:
        self.config = config
        self.state = state or IrcState()
        self.events: asyncio.Queue[IrcMessage] = asyncio.Queue()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._closed = asyncio.Event()
        self._capabilities_seen: set[str] = set()
        self._sasl_active = False

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(
            self.config.host,
            self.config.port,
            ssl=self.config.tls,
        )
        await self._send("CAP", "LS", "302")
        if self.config.password is not None:
            await self._send("PASS", self.config.password)
        await self._send("NICK", self.config.nickname)
        await self._send("USER", self.config.username, "0", "*", self.config.realname)

    async def run(self) -> None:
        """Read until EOF, dispatching each parsed message to state and events."""
        if self._reader is None:
            raise RuntimeError("connect() must be called first")
        try:
            while line := await self._reader.readline():
                try:
                    message = IrcMessage.parse(line)
                except ParseError:
                    continue
                await self._handle_protocol(message)
                self.state.apply(message)
                await self.events.put(message)
        finally:
            self._closed.set()

    async def messages(self) -> AsyncIterator[IrcMessage]:
        while not self._closed.is_set() or not self.events.empty():
            try:
                yield await asyncio.wait_for(self.events.get(), timeout=0.25)
            except asyncio.TimeoutError:
                continue

    async def send(self, message: IrcMessage) -> None:
        """Send an application-selected message; no command dispatch is implied."""
        await self._write(message.to_wire(encoding=self.config.encoding))

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()
        self._closed.set()

    async def _handle_protocol(self, message: IrcMessage) -> None:
        if message.command == "PING":
            await self._send("PONG", *message.params)
            return
        if message.command == "CAP" and len(message.params) >= 3:
            cap_value = " ".join(message.params[2:])
            await self._handle_cap(message.params[1], cap_value)
        elif message.command == "AUTHENTICATE" and self._sasl_active:
            await self._handle_authenticate(message.params[0] if message.params else "")
        elif message.command in {"903", "904", "905", "906", "907", "908"}:
            self._sasl_active = False
            await self._send("CAP", "END")

    async def _handle_cap(self, subcommand: str, value: str) -> None:
        if subcommand == "LS":
            if value.startswith("* "):
                value = value[2:]
            self._capabilities_seen.update(value.split())
            requested = sorted(self.config.capabilities & self._capabilities_seen)
            if self.config.sasl is not None and "sasl" in self._capabilities_seen:
                requested.append("sasl")
            if requested:
                await self._send("CAP", "REQ", " ".join(dict.fromkeys(requested)))
            else:
                await self._send("CAP", "END")
        elif subcommand == "ACK":
            accepted = set(value.split())
            if "sasl" in accepted and self.config.sasl is not None:
                self._sasl_active = True
                await self._send("AUTHENTICATE", self.config.sasl.name)
            else:
                await self._send("CAP", "END")
        elif subcommand == "NAK":
            await self._send("CAP", "END")

    async def _handle_authenticate(self, challenge: str) -> None:
        if challenge == "+" and self.config.sasl is not None:
            await self._send("AUTHENTICATE", self.config.sasl.initial_response())

    async def _send(self, command: str, *params: str) -> None:
        await self._write(IrcMessage(command, params).to_wire(encoding=self.config.encoding))

    async def _write(self, wire: bytes) -> None:
        if self._writer is None:
            raise RuntimeError("connection is not open")
        self._writer.write(wire)
        await self._writer.drain()