# UI Builder

A toolkit-neutral schema and feature registry. Rendering is delegated to a backend adapter. A client supplies a controller and schema; it does not manually bind toolkit events.

```python
builder = SchemaBuilder()
builder.register_feature("profile", "schemas/screens/profile.json", ProfileController())
app = builder.build("profile", backend)
app.run()
```

Schemas contain a widget tree and explicit event bindings:

```json
{
  "children": [{"type": "Button", "id": "connect", "label": "Connect"}],
  "events": [
    {"source": "connect", "event": "button.pressed", "action": "connect_server"}
  ]
}
```

The selected backend translates native events into `UIEvent` values and matches both `event` and `source` before resolving the action. Controllers receive only `UIEvent` and `UIContext`, so the same feature can be rendered by Textual, Qt, Tk, or another adapter. Use `"*"` as the source only for an intentional wildcard binding.

The canonical qivis screen is `schemas/screens/workspace.json`. Standard widget names are resolved by the backend. There is no second JSON widget registry and no example application layer.

The canonical schema service now lives in `qivis.schema`, not in the UI builder. It supports JSON by default, TOML through the standard library, and YAML only when PyYAML is installed. `qivis.config` resolves platform configuration directories and persists server profiles, including TLS, SASL, and autojoin settings.
