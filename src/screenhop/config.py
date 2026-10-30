from __future__ import annotations

import json
from pathlib import Path


DEFAULT = {
    "enabled": False,
    "edge_threshold": 2,
    "enabled_edges": [3],
    "layout_positions": [13, 12, 11, 14],
    "device_address": "",
    "current_host": 1,
    "keyboard_follow": False,
    "autostart": True,
}


class Config:
    def __init__(self) -> None:
        self.path = Path.home() / ".config" / "screenhop" / "config.json"
        self.data = dict(DEFAULT)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

