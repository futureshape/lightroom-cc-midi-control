"""Configuration storage and migration for controller-specific mappings."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


MAPPINGS_FILE = Path("mappings.json")


def empty_config() -> dict:
    return {"midi_port": None, "client_guid": None, "controllers": {}}


def normalize_config(config: dict) -> dict:
    """Upgrade the legacy single-controller format in memory."""
    config = dict(config)
    controllers = config.get("controllers")
    if not isinstance(controllers, dict):
        controllers = {}
    else:
        controllers = {
            str(port): {"mappings": list(profile.get("mappings", []))}
            for port, profile in controllers.items()
            if isinstance(profile, dict)
        }

    legacy_mappings = config.pop("mappings", None)
    port = config.get("midi_port")
    if isinstance(legacy_mappings, list) and port:
        controllers.setdefault(str(port), {"mappings": legacy_mappings})

    config["controllers"] = controllers
    return config


def load_config(path: Path = MAPPINGS_FILE) -> Optional[dict]:
    if not path.exists():
        return None
    return normalize_config(json.loads(path.read_text()))


def save_config(config: dict, path: Path = MAPPINGS_FILE) -> None:
    path.write_text(json.dumps(normalize_config(config), indent=2) + "\n")


def mappings_for(config: dict, port: Optional[str] = None) -> list[dict]:
    """Return the mapping list for a port, creating a blank profile if needed."""
    selected_port = port if port is not None else config.get("midi_port")
    if not selected_port:
        return []
    controllers = config.setdefault("controllers", {})
    profile = controllers.setdefault(str(selected_port), {"mappings": []})
    return profile.setdefault("mappings", [])


def clear_mappings(config: dict, port: Optional[str] = None) -> int:
    """Clear one controller profile and return the number removed."""
    mappings = mappings_for(config, port)
    removed = len(mappings)
    mappings.clear()
    return removed
