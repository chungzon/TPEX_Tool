"""Persistent JSON configuration service."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

_DEFAULTS = {
    "scheduler_enabled": False,
    "scheduler_time": "18:00",
    "scheduler_top_n": 300,
    "stock_codes": [],        # saved stock code list
    "stock_list_date": "",    # when the list was last refreshed
}


class ConfigService:
    """Read/write app settings to a JSON file with defaults fallback."""

    def __init__(self, path: Path | str = _CONFIG_PATH):
        self._path = Path(path)
        self._data: dict = {}
        self.load()

    def load(self) -> dict:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        return self._data

    def save(self) -> None:
        # Atomic write: write to temp then replace
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(self._path))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, key: str) -> Any:
        return self._data.get(key, _DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()
