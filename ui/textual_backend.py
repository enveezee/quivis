"""Textual adapter; application and protocol code must not import Textual."""

from __future__ import annotations

import importlib
import inspect
from typing import Any, Mapping, Type

import textual.containers as containers
import textual.widgets as widgets
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Static, TabPane

from .builder import Feature, SchemaBuilder, UIContext, UIEvent
from ..schema import event_bindings


class TextualContext(UIContext):
    def __init__(self, app: "Quivis") -> None:
        self.app = app
        self._pending: dict[tuple[str, str], Any] = {}

    def get(self, widget_id: str, field: str = "value", default: Any = None) -> Any:
        widget = self.app.query_one(f"#{widget_id}")
        return getattr(widget, field, default)

    def set(self, widget_id: str, field: str, value: Any) -> None:
        try:
            widget = self.app.query_one(f"#{widget_id}")
        except Exception:
            self._pending[(widget_id, field)] = value
            return
        if field == "content" and hasattr(widget, "update"):
            widget.update(value)
        elif field == "options" and hasattr(widget, "set_options"):
            widget.set_options(value)
        else:
            setattr(widget, field, value)

    def apply_pending(self) -> None:
        pending = self._pending
        self._pending = {}
        for (widget_id, field), value in pending.items():
            self.set(widget_id, field, value)

    def notify(self, message: str) -> None:
        self.app.notify(message)

    def open_feature(self, feature: str) -> None:
        self.app.open_feature(feature)

    def open_buffer(self, target: str, activate: bool = False) -> None:
        self.app.open_buffer(target, activate)

    def append_buffer(self, target: str, text: str) -> None:
        self.app.append_buffer(target, text)

    def active_buffer(self) -> str:
        return self.app.active_buffer

    def close_buffer(self, target: str) -> None:
        self.app.close_buffer(target)


class TextualBackend:
    """Render feature schemas and translate toolkit events into UIEvents."""

    def __init__(self, widget_registry: Mapping[str, Type[Any]] | None = None) -> None:
        self.widget_registry = dict(widget_registry or self.default_widget_registry())

    @staticmethod
    def default_widget_registry() -> dict[str, Type[Any]]:
        return {
            "Label": widgets.Label, "Static": widgets.Static, "Input": widgets.Input,
            "Button": widgets.Button, "Checkbox": widgets.Checkbox, "Select": widgets.Select,
            "Footer": widgets.Footer, "Header": widgets.Header, "Tree": widgets.Tree,
            "TabbedContent": widgets.TabbedContent, "TabPane": widgets.TabPane,
            "Horizontal": containers.Horizontal, "Vertical": containers.Vertical,
            "Grid": containers.Grid, "ScrollableContainer": containers.ScrollableContainer,
            "Screen": Screen,
        }

    def build(self, feature: Feature, *, controller: object | None, builder: SchemaBuilder) -> "SchemaApp":
        return SchemaApp(self, feature=feature, controller=controller, builder=builder)

    @staticmethod
    def load_styles(source: str | None) -> str:
        if not source:
            return ""
        from pathlib import Path

        try:
            return Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"unable to read UI styles {source}: {exc}") from exc

    def load_widget(self, reference: Any) -> Type[Any]:
        if reference in self.widget_registry:
            return self.widget_registry[reference]
        module, _, name = str(reference).rpartition(".")
        if not module:
            raise ValueError(f"Unknown widget type: {reference!r}")
        return getattr(importlib.import_module(module), name)


class SchemaScreen(Screen):
    def __init__(self, backend: TextualBackend, schema: Mapping[str, Any], name: str) -> None:
        self.backend = backend
        self.schema = schema
        super().__init__(name=name, id=schema.get("id"))

    def compose(self) -> ComposeResult:
        for child in self.schema.get("children", []):
            yield from self._compose_node(child)

    def _compose_node(self, definition: Mapping[str, Any]) -> ComposeResult:
        widget = self.backend.load_widget(definition.get("type") or definition.get("widget"))
        props = dict(definition.get("props", {}))
        props.update({key: value for key, value in definition.items()
                  if key not in {"type", "widget", "children", "events", "presentation", "props"}})
        instance = widget(**props)
        children = definition.get("children", [])
        if children:
            with instance:
                for child in children:
                    yield from self._compose_node(child)
        else:
            yield instance


class SchemaApp(App):
    def __init__(self, backend: TextualBackend, *, feature: Feature, controller: object | None, builder: SchemaBuilder) -> None:
        self.CSS = backend.load_styles(str(feature.styles) if feature.styles else None)
        super().__init__()
        self.backend = backend
        self.feature = feature
        self.controller = controller
        self.builder = builder
        self.schema = builder.load_schema(feature.schema)
        self.context = TextualContext(self)
        self._feature_screens: dict[str, SchemaScreen] = {}
        self._active_buffer = "server"
        self._buffer_targets: dict[str, str] = {"buffer-server": "server"}
        self._buffer_text: dict[str, list[str]] = {"server": []}
        self._auto_scroll: dict[str, bool] = {"buffer-server": True}
        self._watched_scrolls: set[str] = set()

    def _attach_scroll_watch(self, pane_id: str, container: containers.ScrollableContainer) -> None:
        if pane_id in self._watched_scrolls:
            return
        self._watched_scrolls.add(pane_id)

        def on_scroll_changed(val: float) -> None:
            if container.max_scroll_y > 0:
                self._auto_scroll[pane_id] = (
                    container.is_vertical_scroll_end
                    or (container.max_scroll_y - container.scroll_offset.y <= 1)
                )

        self.watch(container, "scroll_y", on_scroll_changed, init=False)

    def open_feature(self, feature_name: str) -> None:
        feature = self.builder.features[feature_name]
        schema = self.builder.load_schema(feature.schema)
        self.schema = schema
        self.feature = feature
        screen = self._feature_screens.get(feature_name)
        if screen is None:
            screen = SchemaScreen(self.backend, schema, feature_name)
            self._feature_screens[feature_name] = screen
            self.install_screen(screen, feature_name)
            self.switch_screen(feature_name)
        elif screen in self.screen_stack:
            while self.screen is not screen:
                self.pop_screen()
        else:
            self.switch_screen(feature_name)
        self.call_after_refresh(self.context.apply_pending)

    def open_buffer(self, target: str, activate: bool = False) -> None:
        self.context._pending_buffer_targets = getattr(self.context, "_pending_buffer_targets", set())
        self.context._pending_buffer_targets.add(target)
        if activate:
            self.context._pending_active_buffer = target
        self.call_after_refresh(self._ensure_pending_buffers)

    def append_buffer(self, target: str, text: str) -> None:
        pane_id = self._buffer_id(target)
        canonical_target = self._buffer_targets.get(pane_id, target)
        self._buffer_targets[pane_id] = canonical_target
        self.context._pending_buffer_text = getattr(self.context, "_pending_buffer_text", {})
        self._buffer_text.setdefault(canonical_target, []).append(text)
        self.context._pending_buffer_text[canonical_target] = self._buffer_text[canonical_target]
        self.open_buffer(canonical_target)

    def _ensure_pending_buffers(self) -> None:
        if self.screen.id != "workspace-screen":
            return
        tabbed = self.query_one("#buffer-tabs")
        targets = getattr(self.context, "_pending_buffer_targets", set())
        text_by_target = getattr(self.context, "_pending_buffer_text", {})
        existing = {pane.id for pane in tabbed.query(TabPane)}
        for target in list(targets):
            pane_id = self._buffer_id(target)
            canonical_target = self._buffer_targets.get(pane_id, target)
            self._buffer_targets[pane_id] = canonical_target
            content = "\n".join(self._buffer_text.get(canonical_target, text_by_target.get(target, [])))
            if pane_id not in existing:
                scroll_container = containers.ScrollableContainer(
                    Static(content, id=f"{pane_id}-content", markup=False),
                    id=f"{pane_id}-scroll",
                )
                tabbed.add_pane(TabPane(
                    canonical_target,
                    scroll_container,
                    id=pane_id,
                ))
                self._auto_scroll.setdefault(pane_id, True)
                self._attach_scroll_watch(pane_id, scroll_container)
                self.call_after_refresh(scroll_container.scroll_end, animate=False)
            else:
                content_id = "server-buffer" if canonical_target == "server" else f"{pane_id}-content"
                scroll_id = "server-scroll" if canonical_target == "server" else f"{pane_id}-scroll"
                try:
                    scroll_container = tabbed.app.query_one(f"#{scroll_id}", containers.ScrollableContainer)
                    self._attach_scroll_watch(pane_id, scroll_container)
                except Exception:
                    scroll_container = None
                try:
                    static = tabbed.app.query_one(f"#{content_id}")
                    static.update(content)
                    if scroll_container is not None and self._auto_scroll.get(pane_id, True):
                        active = self.query_one("#buffer-tabs").active
                        if active == pane_id:
                            scroll_container.scroll_end(animate=False)
                except Exception:
                    pass
            active = getattr(self.context, "_pending_active_buffer", None)
            if active == target or active == canonical_target:
                tabbed.active = pane_id
        self.context._pending_active_buffer = None

    def close_buffer(self, target: str) -> None:
        if target == "server":
            return
        pane_id = self._buffer_id(target)
        canonical_target = self._buffer_targets.pop(pane_id, target)
        self._buffer_text.pop(canonical_target, None)
        self._buffer_text.pop(target, None)
        self._auto_scroll.pop(pane_id, None)
        self._watched_scrolls.discard(pane_id)
        try:
            self.query_one("#buffer-tabs").remove_pane(pane_id)
        except Exception:
            pass

    @property
    def active_buffer(self) -> str:
        try:
            active = self.query_one("#buffer-tabs").active
            return self._buffer_targets.get(active, self._active_buffer)
        except Exception:
            return self._active_buffer

    @staticmethod
    def _buffer_id(target: str) -> str:
        return "buffer-" + "".join(char if char.isalnum() else "-" for char in target).strip("-").lower()

    def compose(self) -> ComposeResult:
        yield from ()

    def on_mount(self) -> None:
        screen = SchemaScreen(self.backend, self.schema, self.feature.name)
        self._feature_screens[self.feature.name] = screen
        self.install_screen(screen, self.feature.name)
        self.push_screen(self.feature.name)
        self.call_after_refresh(self.context.apply_pending)
        self._dispatch(UIEvent("mount"))

    def on_input_changed(self, event: widgets.Input.Changed) -> None:
        self._dispatch(UIEvent("input.changed", event.input.id, event.value))

    def on_input_submitted(self, event: widgets.Input.Submitted) -> None:
        self._dispatch(UIEvent("input.submitted", event.input.id, event.value))

    def on_select_changed(self, event: widgets.Select.Changed) -> None:
        self._dispatch(UIEvent("select.changed", event.select.id, event.value))

    def on_checkbox_changed(self, event: widgets.Checkbox.Changed) -> None:
        self._dispatch(UIEvent("checkbox.changed", event.checkbox.id, event.value))

    async def on_unmount(self) -> None:
        shutdown = getattr(self.controller, "shutdown", None)
        if shutdown is not None:
            result = shutdown()
            if inspect.isawaitable(result):
                await result

    def on_tabbed_content_tab_activated(self, event: Any) -> None:
        pane_id = event.pane.id if event.pane else ""
        canonical_target = self._buffer_targets.get(pane_id, pane_id.removeprefix("buffer-")) or "server"
        self._active_buffer = canonical_target
        if self._auto_scroll.get(pane_id, True) and event.pane is not None:
            try:
                scroll = event.pane.query_one(containers.ScrollableContainer)
                self.call_after_refresh(scroll.scroll_end, animate=False)
            except Exception:
                pass
        self._dispatch(UIEvent("tab.activated", "buffer-tabs", canonical_target))

    def on_button_pressed(self, event: widgets.Button.Pressed) -> None:
        self._dispatch(UIEvent("button.pressed", event.button.id, getattr(event.button, "value", None)))

    def _dispatch(self, event: UIEvent) -> None:
        for binding in event_bindings(self.schema):
            if binding["event"] != event.name:
                continue
            source = binding["source"]
            if source != "*" and source != event.source_id:
                continue
            callback = getattr(self.controller, binding["action"])
            result = callback(event, self.context)
            if inspect.isawaitable(result):
                self.run_worker(result, exclusive=False)
