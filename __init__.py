"""Quivis IRC (qivis), a toolkit-neutral human IRC client."""

__all__ = ["QivisController", "create_app", "register_features"]


def __getattr__(name: str):
    if name in __all__:
        from .app import QivisController, create_app, register_features

        return {
            "QivisController": QivisController,
            "create_app": create_app,
            "register_features": register_features,
        }[name]
    raise AttributeError(name)