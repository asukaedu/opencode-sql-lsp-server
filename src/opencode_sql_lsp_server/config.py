from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import cast


JsonObject = dict[str, object]


class SqlLspConfigLoadError(RuntimeError):
    pass


def _read_config_json(cfg_path: Path) -> JsonObject:
    loaded = cast(object, json.loads(cfg_path.read_text(encoding="utf-8")))
    if not isinstance(loaded, dict):
        raise SqlLspConfigLoadError(
            f"Failed to load config {cfg_path}: top-level JSON value must be an object"
        )
    loaded_dict = cast(dict[object, object], loaded)
    return {str(key): value for key, value in loaded_dict.items()}


def _positive_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


@dataclass(frozen=True)
class SqlLspConfig:
    default_dialect: str
    overrides: dict[str, str]
    max_lint_lines: int
    max_lint_bytes: int

    @staticmethod
    def default() -> "SqlLspConfig":
        return SqlLspConfig(
            default_dialect="starrocks",
            overrides={},
            max_lint_lines=5_000,
            max_lint_bytes=200_000,
        )

    @staticmethod
    def load(root: Path) -> "SqlLspConfig":
        cfg_path = root / ".opencode" / "sql-lsp.json"
        if not cfg_path.exists():
            return SqlLspConfig.default()

        try:
            data = _read_config_json(cfg_path)
        except Exception as e:
            raise SqlLspConfigLoadError(f"Failed to load config {cfg_path}: {e}") from e
        default_dialect = data.get("defaultDialect")
        if not isinstance(default_dialect, str) or not default_dialect.strip():
            default_dialect = "starrocks"
        overrides_raw = data.get("overrides")
        overrides: dict[str, str] = {}
        if isinstance(overrides_raw, dict):
            overrides_dict = cast(dict[object, object], overrides_raw)
            for raw_key, raw_value in overrides_dict.items():
                k = raw_key
                v = raw_value
                if (
                    isinstance(k, str)
                    and isinstance(v, str)
                    and k.strip()
                    and v.strip()
                ):
                    overrides[k] = v
        default_config = SqlLspConfig.default()
        max_lint_lines = _positive_int(
            data.get("maxLintLines"), default_config.max_lint_lines
        )
        max_lint_bytes = _positive_int(
            data.get("maxLintBytes"), default_config.max_lint_bytes
        )

        return SqlLspConfig(
            default_dialect=default_dialect,
            overrides=overrides,
            max_lint_lines=max_lint_lines,
            max_lint_bytes=max_lint_bytes,
        )

    def dialect_for_path(self, relative_path: str) -> str:
        relative_path_posix = relative_path.replace("\\", "/")
        for pattern, dialect in self.overrides.items():
            pattern_posix = pattern.replace("\\", "/")
            if fnmatch(relative_path_posix, pattern_posix):
                return dialect
        return self.default_dialect
