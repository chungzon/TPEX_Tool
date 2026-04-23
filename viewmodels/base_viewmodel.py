from __future__ import annotations
from typing import Any, Callable


class ObservableProperty:
    """Descriptor that fires change callbacks when a value is set."""

    def __init__(self, default: Any = None):
        self.default = default

    def __set_name__(self, owner: type, name: str):
        self.attr = f"_prop_{name}"
        self.name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        return getattr(obj, self.attr, self.default)

    def __set__(self, obj: Any, value: Any):
        old = self.__get__(obj, type(obj))
        setattr(obj, self.attr, value)
        if old != value:
            obj.notify(self.name, value)


class BaseViewModel:
    """Base class for all ViewModels with observable property support."""

    def __init__(self):
        self._listeners: dict[str, list[Callable]] = {}

    def bind(self, property_name: str, callback: Callable):
        self._listeners.setdefault(property_name, []).append(callback)

    def unbind(self, property_name: str, callback: Callable):
        if property_name in self._listeners:
            self._listeners[property_name].remove(callback)

    def notify(self, property_name: str, value: Any):
        for cb in self._listeners.get(property_name, []):
            cb(value)
