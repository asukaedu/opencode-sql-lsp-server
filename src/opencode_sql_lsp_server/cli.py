from __future__ import annotations

import argparse

from .server import server


class _Args(argparse.Namespace):
    stdio: bool = False
    workspace: str | None = None


def main() -> None:
    parser = argparse.ArgumentParser(prog="opencode-sql-lsp")
    _ = parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run the language server over stdio (recommended)",
    )
    _ = parser.add_argument(
        "--workspace",
        type=str,
        default=None,
        help="Workspace root (used for .opencode/sql-lsp.json); if omitted, uses client rootUri when possible",
    )
    args = parser.parse_args(namespace=_Args())

    if not args.stdio:
        raise SystemExit("Only --stdio transport is supported")

    # pygls gets rootUri from initialize; we optionally allow an override.
    if args.workspace:
        server.set_workspace_root(args.workspace)

    server.start_io()
