# ircproto

`ircproto` is a reactive IRCv3 protocol library for Python 3.11+. It contains no bot command layer and never interprets `PRIVMSG` as an instruction.

## Architecture

```text
socket <-> IrcConnection (asyncio streams)
                 |
                 +--> IrcMessage.parse / to_wire (pure parser)
                 +--> IrcState.apply (in-memory projection, no I/O)
                 +--> asyncio.Queue[IrcMessage] (TUI event stream)
```

- `parser.py` owns only IRC framing, tags, prefixes, parameters, and serialization.
- `state.py` owns connection metadata, users, channels, modes/history extensions, and personal tracking. It can be unit-tested with messages alone.
- `connection.py` owns TCP/TLS, registration, CAP negotiation, SASL PLAIN, mandatory PING/PONG, and event delivery. Application traffic is sent explicitly through `send()`.

## Registration and SASL

The connection sends `CAP LS 302`, optional `PASS`, `NICK`, and `USER` immediately. On `CAP LS`, it requests the configured capabilities and `sasl` when a mechanism is configured. After `CAP ACK sasl`, it sends `AUTHENTICATE PLAIN`, then sends the base64 SASL PLAIN initial response after the server's `AUTHENTICATE +`. CAP negotiation ends with `CAP END` after ACK without SASL, NAK, or a SASL result.

```python
config = ConnectionConfig(
    host="irc.example.org",
    nickname="alice",
    username="alice",
    realname="Alice",
    capabilities=frozenset({"message-tags", "server-time"}),
    sasl=SaslPlain("alice", "secret"),
)
connection = IrcConnection(config)
await connection.connect()
reader_task = asyncio.create_task(connection.run())

async for message in connection.messages():
    # Render messages in the TUI; choose any outgoing action explicitly.
    ...
```

The included state model is intentionally a foundation rather than a policy engine. A production client can extend `IrcState.apply()` for `MODE`, `PART`, `QUIT`, `NICK`, `353` names replies, batch, labeled responses, and network-specific casemapping while retaining the same boundaries.

## Human commands

`commands.py` contains a pure schema-driven parser for client commands. Definitions live in `commands.json`, including aliases, usage/help text, argument limits, and key bindings. The parser does not send anything or know about a UI:

```python
registry = CommandRegistry.from_schema("ircproto/commands.json")
parsed = registry.parse('/msg nick "hello there"')
assert parsed.command.args == ("nick", "hello there")
```

Non-command input is returned as ordinary text. A client application decides how command actions map to protocol operations.