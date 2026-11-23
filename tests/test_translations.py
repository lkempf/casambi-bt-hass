"""Test that the translation files stay in sync."""

import json
from pathlib import Path
import re
from typing import Any

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "casambi_bt"
REFERENCE = re.compile(r"\[%key:[^%]+%\]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat |= _flatten(value, path)
        else:
            flat[path] = value
    return flat


STRINGS = _flatten(_load(COMPONENT / "strings.json"))


@pytest.mark.parametrize(
    "path", sorted((COMPONENT / "translations").glob("*.json")), ids=lambda p: p.name
)
def test_translation_matches_strings(path: Path) -> None:
    """Every translation must have exactly the keys and placeholders of strings.json."""
    translation = _flatten(_load(path))

    assert translation.keys() == STRINGS.keys()
    for key, value in translation.items():
        # Custom integrations don't get references resolved at runtime.
        assert not REFERENCE.search(value), key
        if not REFERENCE.search(STRINGS[key]):
            assert set(PLACEHOLDER.findall(value)) == set(
                PLACEHOLDER.findall(STRINGS[key])
            ), key
