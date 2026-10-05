"""Internal type aliases."""

from __future__ import annotations

import json
from typing import Union, cast

JsonObject = dict[str, 'JsonValue']

JsonValue = Union[
    None,
    bool,
    int,
    float,
    str,
    list['JsonValue'],
    JsonObject,
]

# The json module only ever produces values of these types (when used
# without custom hooks), but typeshed annotates its results as ``Any``.
# These wrappers state that once, so callers get a precise type.
_DECODER = json.JSONDecoder()


def json_loads(s: str | bytes) -> JsonValue:
    """Parse a JSON document, like :func:`json.loads`."""
    return cast(JsonValue, json.loads(s))


def json_raw_decode(s: str, pos: int = 0) -> tuple[JsonValue, int]:
    """Parse one JSON value at *pos*, like :meth:`json.JSONDecoder.raw_decode`.

    Returns ``(value, end_pos)``.
    """
    return cast(tuple[JsonValue, int], _DECODER.raw_decode(s, pos))
