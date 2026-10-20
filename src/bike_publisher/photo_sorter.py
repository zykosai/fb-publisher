from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components


_COMPONENT_DIR = Path(__file__).with_name("photo_sorter_frontend")
_photo_sorter = components.declare_component(
    "bike_publisher_photo_sorter",
    path=str(_COMPONENT_DIR),
)


def photo_sorter(photos: list[dict], *, key: str):
    return _photo_sorter(photos=photos, key=key, default=None)
