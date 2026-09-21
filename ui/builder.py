"""Toolkit-neutral schema and action registration."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..schema import event_bindings, load


@dataclass(frozen=True, slots=True)
class UIEvent:
    """A toolkit-independent user interaction."""

    name: str
    source_id: str | None = None
    value: Any = None
    fields: Mapping[str, Any] = ()


class UIContext:
    """The minimal view API controllers need; implementations belong to backends."""

    def get(self, widget_id: str, field: str = "value", default: Any = None) -> Any:
        raise NotImplementedError

    def set(self, widget_id: str, field: str, value: Any) -> None:
        raise NotImplementedError

    def notify(self, message: str) -> None:
        raise NotImplementedError

    def open_feature(self, feature: str) -> None:
        raise NotImplementedError

    def open_buffer(self, target: str, activate: bool = False) -> None:
        raise NotImplementedError

    def append_buffer(self, target: str, text: str) -> None:
        raise NotImplementedError

    def active_buffer(self) -> str:
        raise NotImplementedError

    def close_buffer(self, target: str) -> None:
        raise NotImplementedError


ControllerFactory = Callable[[], object]


@dataclass(frozen=True, slots=True)
class Feature:
    name: str
    schema: Mapping[str, Any] | str | Path
    controller: object | ControllerFactory | None = None
    styles: str | Path | None = None


class SchemaBuilder:
    """Register feature schemas and delegate rendering to a selected backend."""

    def __init__(self) -> None:
        self.features: dict[str, Feature] = {}

    load_schema = staticmethod(load)

    @staticmethod
    def event_bindings(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        """Return explicit source/event/action bindings from a feature schema."""
        return event_bindings(schema)

    def register(self, feature: Feature) -> None:
        if feature.name in self.features:
            raise ValueError(f"Feature already registered: {feature.name}")
        self.features[feature.name] = feature

    def register_feature(
        self,
        name: str,
        schema: Mapping[str, Any] | str | Path,
        controller: object | ControllerFactory | None = None,
        styles: str | Path | None = None,
    ) -> None:
        self.register(Feature(name, schema, controller, styles))

    def build(self, feature: str, backend: Any) -> Any:
        try:
            definition = self.features[feature]
        except KeyError as exc:
            raise KeyError(f"Unknown UI feature: {feature}") from exc
        controller = definition.controller
        if inspect.isfunction(controller) or inspect.isclass(controller):
            controller = controller()
        return backend.build(definition, controller=controller, builder=self)
