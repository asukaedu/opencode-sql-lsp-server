from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SqlLspConfig:
    default_dialect: str
    overrides: dict[str, str]

    @staticmethod
    def load(root: Path) -> "SqlLspConfig":
        cfg_path = root / ".opencode" / "sql-lsp.json"
        if not cfg_path.exists():
            return SqlLspConfig(default_dialect="starrocks", overrides={})
        data: Any = json.loads(cfg_path.read_text(encoding="utf-8"))
        default_dialect = data.get("defaultDialect")
        if not isinstance(default_dialect, str) or not default_dialect.strip():
            default_dialect = "starrocks"
        overrides_raw = data.get("overrides")
        overrides: dict[str, str] = {}
        if isinstance(overrides_raw, dict):
            for k, v in overrides_raw.items():
                if (
                    isinstance(k, str)
                    and isinstance(v, str)
                    and k.strip()
                    and v.strip()
                ):
                    overrides[k] = v
        return SqlLspConfig(default_dialect=default_dialect, overrides=overrides)

    def dialect_for_path(self, relative_path: str) -> str:
        for pattern, dialect in self.overrides.items():
            if fnmatch(relative_path, pattern):
                return dialect
        return self.default_dialect
