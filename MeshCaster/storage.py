from __future__ import annotations
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


class JsonStore:
    """Атомарное чтение/запись небольших JSON-файлов.

    save() не бросает исключений: вызывается из GUI-слотов, где
    необработанная ошибка уронила бы всё приложение.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, default: Any) -> Any:
        if not self.path.exists():
            return default
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError, TypeError):
            return default

    def save(self, value: Any) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=self.path.name + ".",
                suffix=".tmp",
                dir=str(self.path.parent),
            )
        except OSError:
            return False
        try:
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(value, fh, ensure_ascii=False, indent=2)
                    fh.flush()
            except (OSError, TypeError, ValueError):
                return False
            # На Windows os.replace может временно падать (антивирус держит
            # файл) — несколько попыток с паузой.
            for attempt in range(5):
                try:
                    os.replace(tmp_name, self.path)
                    return True
                except OSError:
                    time.sleep(0.05 * (2 ** attempt))
            return False
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
