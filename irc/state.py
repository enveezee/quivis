"""Passive in-memory IRC state. This module performs no network I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .parser import IrcMessage


class CaseInsensitiveDict(dict):
    """Dictionary with case-insensitive string keys, preserving latest key casing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._keys: dict[str, str] = {}
        super().__init__()
        self.update(*args, **kwargs)

    @staticmethod
    def _k(key: Any) -> Any:
        return key.casefold() if isinstance(key, str) else key

    def __setitem__(self, key: Any, value: Any) -> None:
        folded = self._k(key)
        old_key = self._keys.get(folded)
        if old_key is not None and old_key != key:
            super().pop(old_key, None)
        self._keys[folded] = key
        super().__setitem__(key, value)

    def __getitem__(self, key: Any) -> Any:
        folded = self._k(key)
        real_key = self._keys.get(folded, key)
        return super().__getitem__(real_key)

    def __contains__(self, key: Any) -> bool:
        return self._k(key) in self._keys

    def get(self, key: Any, default: Any = None) -> Any:
        folded = self._k(key)
        real_key = self._keys.get(folded)
        if real_key is None:
            return default
        return super().get(real_key, default)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        folded = self._k(key)
        real_key = self._keys.get(folded)
        if real_key is not None:
            return super().__getitem__(real_key)
        self[key] = default
        return default

    def pop(self, key: Any, *args: Any) -> Any:
        folded = self._k(key)
        real_key = self._keys.pop(folded, None)
        if real_key is None:
            if args:
                return args[0]
            raise KeyError(key)
        return super().pop(real_key)

    def __delitem__(self, key: Any) -> None:
        folded = self._k(key)
        real_key = self._keys.pop(folded, None)
        if real_key is None:
            raise KeyError(key)
        super().__delitem__(real_key)

    def clear(self) -> None:
        self._keys.clear()
        super().clear()

    def update(self, *args: Any, **kwargs: Any) -> None:
        if args:
            other = args[0]
            if isinstance(other, dict):
                for k, v in other.items():
                    self[k] = v
            else:
                for k, v in other:
                    self[k] = v
        for k, v in kwargs.items():
            self[k] = v


@dataclass(slots=True)
class User:
    nick: str
    user: str | None = None
    host: str | None = None
    account: str | None = None


@dataclass(slots=True)
class Channel:
    name: str
    topic: str | None = None
    users: dict[str, User] = field(default_factory=CaseInsensitiveDict)
    modes: dict[str, str | None] = field(default_factory=dict)
    history: list[IrcMessage] = field(default_factory=list)


class IrcState:
    """A deliberately incomplete but extensible projection of server state."""

    def __init__(self) -> None:
        self.connected = False
        self.network: str | None = None
        self.nick: str | None = None
        self.channels: dict[str, Channel] = CaseInsensitiveDict()
        self.users: dict[str, User] = CaseInsensitiveDict()
        self.capabilities: set[str] = set()
        self.isupport: dict[str, str | None] = {}
        self.received: list[IrcMessage] = []
        self.updated_at: datetime | None = None

    def apply(self, message: IrcMessage) -> None:
        """Apply an incoming message without sending anything in response."""
        self.received.append(message)
        self.updated_at = datetime.now(timezone.utc)
        command = message.command
        if command == "001" and message.params:
            self.connected = True
            self.nick = message.params[0]
        elif command == "005":
            for token in message.params[1:]:
                key, separator, value = token.partition("=")
                if key.startswith("-"):
                    self.isupport.pop(key[1:], None)
                else:
                    self.isupport[key] = value if separator else None
        elif command == "CAP" and len(message.params) >= 3:
            if message.params[1] == "ACK":
                self.capabilities.update(message.params[2].split())
            elif message.params[1] == "DEL":
                self.capabilities.difference_update(message.params[2].split())
        elif command == "JOIN" and message.prefix and message.params:
            channel = self.channels.setdefault(message.params[0], Channel(message.params[0]))
            user = self._user_from_prefix(message.prefix)
            channel.users[user.nick] = user
            self.users[user.nick] = user
        elif command == "PART" and message.prefix and message.params:
            channel = self.channels.get(message.params[0])
            if channel is not None:
                nick = self._user_from_prefix(message.prefix).nick
                channel.users.pop(nick, None)
        elif command == "KICK" and len(message.params) >= 2:
            channel = self.channels.get(message.params[0])
            if channel is not None:
                channel.users.pop(message.params[1], None)
        elif command == "QUIT" and message.prefix:
            nick = self._user_from_prefix(message.prefix).nick
            self.users.pop(nick, None)
            for channel in self.channels.values():
                channel.users.pop(nick, None)
        elif command == "NICK" and message.prefix and message.params:
            old_nick = self._user_from_prefix(message.prefix).nick
            new_nick = message.params[0]
            user = self.users.pop(old_nick, User(new_nick))
            user.nick = new_nick
            self.users[new_nick] = user
            if self.nick and old_nick.casefold() == self.nick.casefold():
                self.nick = new_nick
            for channel in self.channels.values():
                if old_nick in channel.users:
                    channel.users[new_nick] = channel.users.pop(old_nick)
        elif command == "353" and len(message.params) >= 4:
            channel_name = message.params[2]
            channel = self.channels.setdefault(channel_name, Channel(channel_name))
            for raw_nick in message.params[3].split():
                nick = raw_nick.lstrip("~&@%+!")
                if nick:
                    channel.users.setdefault(nick, User(nick))
                    self.users.setdefault(nick, channel.users[nick])
        elif command == "PRIVMSG" and len(message.params) >= 2:
            target = message.params[0]
            if target.startswith(("#", "&", "+", "!")):
                self.channels.setdefault(target, Channel(target)).history.append(message)
        elif command == "TOPIC" and len(message.params) >= 2:
            channel = self.channels.setdefault(message.params[0], Channel(message.params[0]))
            channel.topic = message.params[1]

    @staticmethod
    def _user_from_prefix(prefix: str) -> User:
        nick, _, remainder = prefix.partition("!")
        user, _, host = remainder.partition("@")
        return User(nick, user or None, host or None)