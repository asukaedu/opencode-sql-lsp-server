from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pygls.exceptions import PyglsError
from pygls.uris import to_fs_path

from .config import SqlLspConfig


@dataclass
class ConfigCacheEntry:
    mtime_ns: int | None
    config: SqlLspConfig
    last_error_mtime_ns: int | None = None


ReportServerError = Callable[[Exception, type[PyglsError]], None]


def best_root_for_uri(workspace_roots: list[Path], doc_uri: str) -> Path | None:
    if not workspace_roots:
        return None
    try:
        fs_path = to_fs_path(doc_uri)
        if not fs_path:
            return None
        path = Path(fs_path).resolve()
    except Exception:
        return None

    best: Path | None = None
    best_len = -1
    for root in workspace_roots:
        try:
            _ = path.relative_to(root)
        except Exception:
            continue
        root_len = len(str(root))
        if root_len > best_len:
            best = root
            best_len = root_len
    return best


def config_cache_entry_for_root(
    root: Path,
    *,
    config_cache: dict[Path, ConfigCacheEntry],
    report_server_error: ReportServerError,
) -> ConfigCacheEntry:
    cfg_path = root / ".opencode" / "sql-lsp.json"
    try:
        stat_result = cfg_path.stat()
        mtime_ns: int | None = stat_result.st_mtime_ns
    except FileNotFoundError:
        mtime_ns = None
    except Exception as error:
        report_server_error(error, PyglsError)
        return ConfigCacheEntry(mtime_ns=None, config=SqlLspConfig.default())

    cached = config_cache.get(root)
    if cached and cached.mtime_ns == mtime_ns:
        return cached

    if mtime_ns is None:
        entry = ConfigCacheEntry(mtime_ns=None, config=SqlLspConfig.default())
        config_cache[root] = entry
        return entry

    try:
        config = SqlLspConfig.load(root)
    except Exception as error:
        if cached and cached.config:
            if cached.last_error_mtime_ns != mtime_ns:
                cached.last_error_mtime_ns = mtime_ns
                report_server_error(error, PyglsError)
            return cached
        report_server_error(error, PyglsError)
        config = SqlLspConfig.default()

    entry = ConfigCacheEntry(mtime_ns=mtime_ns, config=config)
    config_cache[root] = entry
    return entry


def config_for_root(
    root: Path,
    *,
    config_cache: dict[Path, ConfigCacheEntry],
    report_server_error: ReportServerError,
) -> SqlLspConfig:
    return config_cache_entry_for_root(
        root,
        config_cache=config_cache,
        report_server_error=report_server_error,
    ).config
