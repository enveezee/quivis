# Quivis IRC

Server profiles are stored in the platform configuration directory, or in `QIVIS_CONFIG_DIR` when set. JSON is the default persistence format and contains the server address, port, nickname, TLS, SASL PLAIN settings, and autojoin channels.

The current Textual launcher is:

```bash
python3 -m qivis
```

Only `__main__.py` selects Textual. qivis controllers and schemas remain backend-neutral.