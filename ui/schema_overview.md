# Shared Schema Overview

The shared schema loader lives in `schema.py`. It accepts mappings, JSON, TOML, and optional YAML, returning one canonical mapping shape. UI features, command definitions, and future client features use the same loader and validation boundary.

The builder registers feature schemas and controllers. A backend renders them and dispatches normalized events; the controller does not manually wire toolkit callbacks.

## Canonical screen

Source: `ui/schemas/status.json`, `ui/schemas/server_settings.json`, and `ui/schemas/workspace.json`

The current screen contains:

- a connection bar for server address, port, nickname, TLS, and connect
- channel and user navigation trees
- a chat log and `ChatInput`
- a status line and footer

Its event contract is source-specific:

- `connect` + `button.pressed` -> `connect_server`
- `chat-input` + `input.submitted` -> `send_message`

## Builder rules

1. Widget names resolve through the standard registry or a fully qualified import path.
2. Screen definitions are nested mappings with `type`, `id`, `children`, and optional `events`.
3. Runtime callbacks are named in the schema and resolved on the controller.
4. Async operations stay in the controller and protocol layers.
5. The protocol engine has no dependency on Textual.

## Command schemas

Human IRC commands are defined in `irc/commands.json`. Each command declares its canonical name, aliases, usage, description, action, and argument bounds. The parser uses `shlex`, so quoted message arguments remain intact.

```json
{
	"name": "msg",
	"aliases": ["privmsg"],
	"usage": "msg <target> <message>",
	"action": "msg",
	"min_args": 2
}
```

Key bindings use the same declarative file and resolve to application actions independently of the UI toolkit.

## Current layout

- `ui/schemas/` — qirc feature screens and qirc.css
- `app.py` — thin Textual host and protocol-facing controller
- backend adapters — toolkit-specific widget registries and event translation
- `irc/` — transport, parser, and state engine with no UI dependency
