"""Internal type aliases."""

from __future__ import annotations

from typing import Union

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
