"""Quivis IRC application controller and feature registration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from .irc import ConnectionConfig, IrcConnection, IrcMessage, IrcState, SaslPlain
from .irc.commands import CommandError, CommandRegistry
from .ui.builder import SchemaBuilder, UIContext, UIEvent

from .config import load_profiles, save_profiles
from .schema import SchemaError, load

ROOT = Path(__file__).parent
COMMAND_SCHEMA = ROOT / "irc" / "commands.json"
STYLES = ROOT / "ui" / "schemas" / "styles" / "quivis.css"
WORKSPACE_SCHEMA = ROOT / "ui" / "schemas" / "screens" / "workspace.json"


class QivisController:
    """IRC behavior, profiles, and feature actions without GUI toolkit imports."""

    def __init__(self) -> None:
        self.connection: IrcConnection | None = None
        self.reader_task: asyncio.Task[None] | None = None
        self.event_task: asyncio.Task[None] | None = None
        self.commands = CommandRegistry.from_schema(COMMAND_SCHEMA)
        self.current_target = "#lobby"
        self.transcript: list[str] = []
        self.pending_autojoin: list[str] = []
        self.presentation = load(WORKSPACE_SCHEMA).get("presentation", {})
        self.config_error: str | None = None
        try:
            self.profiles: dict[str, Any] = load_profiles().get("profiles", {})
        except SchemaError as exc:
            self.profiles = {}
            self.config_error = str(exc)
        self.active_profile: dict[str, Any] | None = None

    def open_server_settings(self, event: UIEvent, ui: UIContext) -> None:
        ui.open_feature("server_settings")
        ui.set("profile-selector", "options", [(name, name) for name in sorted(self.profiles)])
        if len(self.profiles) == 1:
            self.load_server_profile(UIEvent("select.changed", "profile-selector", next(iter(self.profiles))), ui)
        if self.config_error:
            ui.notify(f"Profile configuration unavailable: {self.config_error}")

    def load_server_profile(self, event: UIEvent, ui: UIContext) -> None:
        name = event.value
        if not name or name not in self.profiles:
            return
        profile = self.profiles[name]
        values = {
            "profile-name": profile.get("name", name),
            "server-address": profile.get("address", ""),
            "server-port": str(profile.get("port", 6697)),
            "nickname": profile.get("nickname", ""),
            "realname": profile.get("realname", ""),
            "use-tls": profile.get("tls", True),
            "use-sasl": profile.get("sasl", {}).get("enabled", False),
            "sasl-username": profile.get("sasl", {}).get("username", ""),
            "sasl-password": profile.get("sasl", {}).get("password", ""),
            "autojoin": ", ".join(profile.get("autojoin", [])),
        }
        for widget_id, value in values.items():
            ui.set(widget_id, "value", value)

    def open_status(self, event: UIEvent, ui: UIContext) -> None:
        ui.open_feature("status")

    def save_server_settings(self, event: UIEvent, ui: UIContext) -> None:
        profile = self._read_profile(ui)
        self.profiles[profile["name"]] = profile
        try:
            save_profiles({"profiles": self.profiles})
        except SchemaError as exc:
            ui.notify(f"Could not save profile: {exc}")
            return
        ui.notify(f"Saved server profile: {profile['name']}")

    async def save_and_connect(self, event: UIEvent, ui: UIContext) -> None:
        self.save_server_settings(event, ui)
        await self.connect_profile(self._read_profile(ui), ui)

    async def connect_saved_server(self, event: UIEvent, ui: UIContext) -> None:
        if not self.profiles:
            ui.notify("Create a server profile first")
            ui.open_feature("server_settings")
            return
        profile = next(iter(self.profiles.values()))
        await self.connect_profile(profile, ui)

    async def connect_profile(self, profile: dict[str, Any], ui: UIContext) -> None:
        await self.disconnect()
        try:
            port = int(profile.get("port", 6697))
        except (TypeError, ValueError):
            ui.notify("Server port must be a number")
            return
        sasl = None
        if profile.get("sasl", {}).get("enabled"):
            sasl_data = profile["sasl"]
            sasl = SaslPlain(sasl_data.get("username", ""), sasl_data.get("password", ""))
        self.active_profile = profile
        self.connection = IrcConnection(ConnectionConfig(
            host=profile.get("address", ""),
            port=port,
            nickname=profile.get("nickname", "guest"),
            username=profile.get("username", profile.get("nickname", "guest")),
            realname=profile.get("realname", profile.get("nickname", "guest")),
            tls=bool(profile.get("tls", True)),
            capabilities=frozenset({"message-tags", "server-time"}),
            sasl=sasl,
        ), state=IrcState())
        # Status owns connection progress and failure messages. Move there
        # before touching status widgets when invoked from settings.
        ui.open_feature("status")
        ui.set("connection-status", "content", f"Connecting to {profile.get('address')}:{port}")
        try:
            await self.connection.connect()
        except (ConnectionError, OSError) as exc:
            ui.set("connection-status", "content", f"Connection failed: {exc}")
            return
        # The workspace is the live client surface. Registration traffic is
        # rendered into its server buffer while the connection completes.
        ui.open_feature("workspace")
        ui.set("connection-status", "content", f"Connecting to {profile.get('address')}:{port}")
        self.reader_task = asyncio.create_task(self.connection.run())
        self.event_task = asyncio.create_task(self._consume_events(ui, self.connection))
        autojoin = profile.get("autojoin", [])
        if autojoin:
            self.current_target = autojoin[0]
        self.pending_autojoin = list(autojoin)

    async def send_message(self, event: UIEvent, ui: UIContext) -> None:
        if self.connection is None:
            ui.notify("Not connected")
            return
        text = str(event.value or "").strip()
        if not text:
            return
        self.current_target = ui.active_buffer() or self.current_target
        try:
            parsed = self.commands.parse(text)
            if parsed.command is None:
                message = IrcMessage("PRIVMSG", (self.current_target, parsed.text or ""))
                await self.connection.send(message)
                self.render_message(ui, message, local=True)
            else:
                await self._run_command(parsed.command.spec.action, parsed.command.args, ui)
        except CommandError as exc:
            ui.notify(str(exc))
        ui.set("chat-input", "value", "")

    async def _run_command(self, action: str, args: tuple[str, ...], ui: UIContext) -> None:
        if self.connection is None:
            return
        if action == "join":
            self.current_target = args[0]
            await self.connection.send(IrcMessage("JOIN", (args[0],)))
        elif action == "part":
            target = args[0] if args else self.current_target
            await self.connection.send(IrcMessage("PART", (target,)))
            ui.close_buffer(target)
        elif action == "msg":
            self.current_target = args[0]
            ui.open_buffer(args[0], activate=True)
            message = IrcMessage("PRIVMSG", (args[0], " ".join(args[1:])))
            await self.connection.send(message)
            self.render_message(ui, message, local=True)
        elif action == "query":
            self.current_target = args[0]
            ui.open_buffer(args[0], activate=True)
            ui.notify(f"Private conversation: {args[0]}")
        elif action == "whois":
            await self.connection.send(IrcMessage("WHOIS", (args[0],)))
            ui.open_buffer(args[0], activate=True)
        elif action == "close":
            target = args[0] if args else ui.active_buffer()
            if target.startswith(("#", "&", "+", "!")):
                await self.connection.send(IrcMessage("PART", (target,)))
            ui.close_buffer(target)
        elif action == "nick":
            await self.connection.send(IrcMessage("NICK", (args[0],)))
        elif action == "quit":
            await self.connection.send(IrcMessage("QUIT", (args[0] if args else "quivis",)))
        elif action == "help":
            ui.notify(self.commands.help(args[0] if args else None))

    async def _consume_events(self, ui: UIContext, connection: IrcConnection) -> None:
        try:
            async for message in connection.messages():
                self.render_message(ui, message)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as exc:
            self._safe_set(ui, "connection-status", "content", f"Connection closed: {exc}")

    @staticmethod
    def _display_nick(message: IrcMessage) -> str:
        return (message.prefix or "server").split("!", 1)[0]

    @staticmethod
    def _format_message(message: IrcMessage) -> str:
        if message.command == "TAGMSG":
            return ""
        if message.command == "PRIVMSG" and len(message.params) >= 2:
            return f"<{QivisController._display_nick(message)}> {message.params[1]}"
        if message.command == "JOIN" and message.params:
            nick = (message.prefix or "server").split("!", 1)[0]
            return f"* {nick} joined {message.params[0]}"
        if message.command == "PART" and message.params:
            nick = (message.prefix or "server").split("!", 1)[0]
            return f"* {nick} left {message.params[0]}"
        if message.command == "KICK" and len(message.params) >= 2:
            nick = (message.prefix or "server").split("!", 1)[0]
            target_user = message.params[1]
            reason = f" ({message.params[2]})" if len(message.params) > 2 else ""
            return f"* {target_user} was kicked by {nick}{reason}"
        if message.command == "QUIT":
            nick = (message.prefix or "server").split("!", 1)[0]
            return f"* {nick} quit"
        if message.command == "NICK" and message.params:
            nick = (message.prefix or "server").split("!", 1)[0]
            return f"* {nick} is now known as {message.params[0]}"
        if message.command == "353" and len(message.params) >= 4:
            return f"Names for {message.params[2]}: {message.params[3]}"
        if message.command == "001":
            return f"Connected: {' '.join(message.params[1:])}".strip()
        if message.command.isdigit():
            return f"[{message.command}] {' '.join(message.params[1:] or message.params)}".strip()
        return f"{message.prefix or 'server'} {message.command} {' '.join(message.params)}".strip()

    @staticmethod
    def _message_target(message: IrcMessage) -> str:
        if message.command in {"332", "333", "353", "366"}:
            index = 2 if message.command == "353" else 1
            if len(message.params) > index:
                return message.params[index]
        if message.command in {"311", "312", "313", "317", "318", "319", "330", "401", "406"}:
            if len(message.params) > 1:
                return message.params[1]
        if message.params and message.params[0].startswith(("#", "&", "+", "!")):
            return message.params[0]
        if message.command in {"PRIVMSG", "NOTICE"} and message.params:
            target = message.params[0]
            if target.startswith(("#", "&", "+", "!")):
                return target
            if message.command == "NOTICE" and "!" not in (message.prefix or ""):
                return "server"
            return (message.prefix or target).split("!", 1)[0]
        return "server"

    def _handle_tagmsg(self, ui: UIContext, message: IrcMessage) -> None:
        """Handle IRCv3 TAGMSG without outputting raw commands into chat buffers."""
        pass

    def on_buffer_activated(self, event: UIEvent, ui: UIContext) -> None:
        target = str(event.value or "server")
        self.current_target = target
        self.refresh_user_list(ui, target)

    def refresh_user_list(self, ui: UIContext, target: str | None = None) -> None:
        if self.connection is None:
            self._safe_set(ui, "user-list", "content", "Users")
            return
        target = target or ui.active_buffer() or self.current_target
        channel = self.connection.state.channels.get(target) if target else None
        if channel is not None and channel.users:
            self._safe_set(
                ui,
                "user-list",
                "content",
                "Users\n" + "\n".join(sorted(channel.users)),
            )
        else:
            self._safe_set(ui, "user-list", "content", "Users")

    def render_message(self, ui: UIContext, message: IrcMessage, *, local: bool = False) -> None:
        if message.command in {"PING", "PONG"} and not local:
            return
        if message.command == "TAGMSG":
            self._handle_tagmsg(ui, message)
            return
        target = self._message_target(message)
        formatted = self._format_message(message)
        if local and message.command == "PRIVMSG" and len(message.params) >= 2:
            nick = self.connection.state.nick if self.connection is not None else "me"
            formatted = f"<{nick or 'me'}> {message.params[1]}"
        timestamp_format = self.presentation.get("timestamp_format", "%H:%M")
        try:
            timestamp = datetime.now().strftime(timestamp_format)
        except (TypeError, ValueError):
            timestamp = datetime.now().strftime("%H:%M")
        line = f"{timestamp} {formatted}" if timestamp_format else formatted
        self.transcript.append(line)
        self.transcript = self.transcript[-200:]
        ui.append_buffer(target, line)
        if message.command == "001":
            self._safe_set(ui, "connection-status", "content", "Connected")
            if self.connection is not None and self.pending_autojoin:
                channels = self.pending_autojoin
                self.pending_autojoin = []
                asyncio.create_task(self._autojoin(channels, self.connection))
        if self.connection is not None:
            if message.command == "JOIN" and message.params:
                channel_name = message.params[0]
                is_me = bool(
                    self.connection.state.nick
                    and self._display_nick(message).casefold() == self.connection.state.nick.casefold()
                )
                should_activate = is_me and (
                    channel_name.casefold() == self.current_target.casefold()
                    or self.current_target in {"#lobby", "server"}
                )
                ui.open_buffer(channel_name, activate=should_activate)
                if should_activate:
                    self.current_target = channel_name
            self.refresh_user_list(ui)

    async def _autojoin(self, channels: list[str], connection: IrcConnection) -> None:
        for channel in channels:
            await connection.send(IrcMessage("JOIN", (channel,)))

    @staticmethod
    def _safe_set(ui: UIContext, widget_id: str, field: str, value: str) -> None:
        try:
            ui.set(widget_id, field, value)
        except (LookupError, RuntimeError):
            # The status/workspace transition is asynchronous; retain the
            # transcript and let the next message repaint once mounted.
            pass

    @staticmethod
    def _read_profile(ui: UIContext) -> dict[str, Any]:
        channels = [item.strip() for item in str(ui.get("autojoin", default="")).split(",") if item.strip()]
        return {
            "name": str(ui.get("profile-name", default="default")).strip() or "default",
            "address": str(ui.get("server-address", default="irc.libera.chat")).strip(),
            "port": int(str(ui.get("server-port", default="6697")).strip() or 6697),
            "nickname": str(ui.get("nickname", default="guest")).strip() or "guest",
            "realname": str(ui.get("realname", default="quivis user")).strip() or "quivis user",
            "tls": bool(ui.get("use-tls", default=True)),
            "sasl": {
                "enabled": bool(ui.get("use-sasl", default=False)),
                "username": str(ui.get("sasl-username", default="")),
                "password": str(ui.get("sasl-password", default="")),
            },
            "autojoin": channels,
        }

    async def disconnect(self) -> None:
        for task in (self.reader_task, self.event_task):
            if task is not None:
                task.cancel()
        self.reader_task = None
        self.event_task = None
        if self.connection is not None:
            close = getattr(self.connection, "close", None)
            if close is not None:
                await close()
            self.connection = None

    async def shutdown(self) -> None:
        if self.connection is not None:
            try:
                await self.connection.send(IrcMessage("QUIT", ("quivis exiting",)))
            except (ConnectionError, OSError, RuntimeError):
                pass
        await self.disconnect()


def register_features(builder: SchemaBuilder, controller: QivisController | None = None) -> QivisController:
    controller = controller or QivisController()
    builder.register_feature("status", ROOT / "ui" / "schemas" / "screens" / "status.json", controller, STYLES)
    builder.register_feature("server_settings", ROOT / "ui" / "schemas" / "screens" / "server_settings.json", controller, STYLES)
    builder.register_feature("workspace", ROOT / "ui" / "schemas" / "screens" / "workspace.json", controller, STYLES)
    return controller


def create_app(backend: object):
    builder = SchemaBuilder()
    register_features(builder)
    return builder.build("status", backend)
