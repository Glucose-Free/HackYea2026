from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from gateway.guards.contract import Guard

GUARD_ENTRY_POINT_GROUP = "ai_gateway.guards"
UNKNOWN_GUARD_TYPE_ERROR = "unknown guard type {type_name!r}; known types: {known}"
ENTRY_POINT_NAME_MISMATCH_ERROR = "entry point {entry_point_name!r} loads guard with type_name {type_name!r}"


class UnknownGuardTypeError(ValueError):
    pass


@dataclass(frozen=True)
class GuardTypeInfo:
    type_name: str
    settings_schema: dict[str, Any]


class GuardRegistry:
    def __init__(self, guard_classes: Mapping[str, type[Guard[Any]]]):
        self._guard_classes = dict(guard_classes)

    @classmethod
    def load_from_entry_points(cls) -> "GuardRegistry":
        guard_classes = {}
        for entry_point in entry_points(group=GUARD_ENTRY_POINT_GROUP):
            guard_class = entry_point.load()
            if guard_class.type_name != entry_point.name:
                raise ValueError(ENTRY_POINT_NAME_MISMATCH_ERROR.format(entry_point_name=entry_point.name, type_name=guard_class.type_name))
            guard_classes[entry_point.name] = guard_class
        return cls(guard_classes)

    def get_guard_class(self, type_name: str) -> type[Guard[Any]]:
        if type_name not in self._guard_classes:
            raise UnknownGuardTypeError(UNKNOWN_GUARD_TYPE_ERROR.format(type_name=type_name, known=sorted(self._guard_classes)))
        return self._guard_classes[type_name]

    def list_guard_types(self) -> list[GuardTypeInfo]:
        return [
            GuardTypeInfo(type_name, guard_class.settings_model.model_json_schema())
            for type_name, guard_class in sorted(self._guard_classes.items())
        ]
