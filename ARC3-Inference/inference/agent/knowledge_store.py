"""Atomic persistence for the structured ARC knowledge store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inference.agent.knowledge_runtime import (
    normalize_knowledge,
    transition_knowledge_level,
)


def load_knowledge(path: Path, *, level: int) -> dict[str, Any]:
    try:
        source = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        source = {}
    return transition_knowledge_level(normalize_knowledge(source, level), level)


def write_knowledge(path: Path, value: dict[str, Any]) -> None:
    level = value.get("current_level", 1) if isinstance(value, dict) else 1
    payload = normalize_knowledge(value, level)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)
