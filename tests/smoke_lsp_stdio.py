from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RpcMessage:
    payload: dict

    def to_bytes(self) -> bytes:
        body = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body


def read_rpc(stream) -> dict:
    header_bytes = b""
    while b"\r\n\r\n" not in header_bytes:
        chunk = stream.read(1)
        if not chunk:
            raise RuntimeError("EOF while reading headers")
        header_bytes += chunk
    header_text = header_bytes.decode("ascii", errors="replace")
    content_length = None
    for line in header_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    if content_length is None:
        raise RuntimeError(f"Missing Content-Length header: {header_text!r}")
    body = stream.read(content_length)
    if len(body) != content_length:
        raise RuntimeError("EOF while reading body")
    return json.loads(body.decode("utf-8"))


def read_until_response(stream, request_id: int) -> dict:
    while True:
        msg = read_rpc(stream)
        if msg.get("id") == request_id:
            return msg


def main() -> int:
    workspace = Path(os.environ.get("WORKSPACE", "/tmp/sql-lsp-workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".opencode").mkdir(exist_ok=True)
    cfg = {
        "defaultDialect": "starrocks",
        "overrides": {"**/*.trino.sql": "trino"},
    }
    (workspace / ".opencode" / "sql-lsp.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8"
    )
    trino_file = workspace / "test.trino.sql"
    starrocks_file = workspace / "test.sql"
    trino_file.write_text("SELECT 1\n", encoding="utf-8")
    starrocks_file.write_text("SELECT 1\n", encoding="utf-8")

    proc = subprocess.Popen(
        ["opencode-sql-lsp", "--stdio", "--workspace", str(workspace)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Failed to open stdio pipes")
    stdin = proc.stdin
    stdout = proc.stdout

    req_id = 1
    initialize = RpcMessage(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "processId": None,
                "rootUri": workspace.as_uri(),
                "capabilities": {},
            },
        }
    )
    stdin.write(initialize.to_bytes())
    stdin.flush()

    msg = read_until_response(stdout, req_id)
    assert msg.get("id") == req_id, msg

    initialized = RpcMessage({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    stdin.write(initialized.to_bytes())
    stdin.flush()

    def did_open(path: Path, language_id: str = "sql") -> None:
        nonlocal req_id
        open_msg = RpcMessage(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/didOpen",
                "params": {
                    "textDocument": {
                        "uri": path.as_uri(),
                        "languageId": language_id,
                        "version": 1,
                        "text": path.read_text(encoding="utf-8"),
                    }
                },
            }
        )
        stdin.write(open_msg.to_bytes())
        stdin.flush()

    did_open(trino_file)
    did_open(starrocks_file)

    req_id += 1
    fmt_req = RpcMessage(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "textDocument/formatting",
            "params": {
                "textDocument": {"uri": trino_file.as_uri()},
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        }
    )
    stdin.write(fmt_req.to_bytes())
    stdin.flush()
    fmt_resp = read_until_response(stdout, req_id)
    assert fmt_resp.get("id") == req_id, fmt_resp
    assert isinstance(fmt_resp.get("result"), list), fmt_resp

    req_id += 1
    fmt_req2 = RpcMessage(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "textDocument/formatting",
            "params": {
                "textDocument": {"uri": starrocks_file.as_uri()},
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        }
    )
    stdin.write(fmt_req2.to_bytes())
    stdin.flush()
    fmt_resp2 = read_until_response(stdout, req_id)
    assert fmt_resp2.get("id") == req_id, fmt_resp2
    assert isinstance(fmt_resp2.get("result"), list), fmt_resp2

    shutdown_id = req_id + 1
    shutdown = RpcMessage(
        {"jsonrpc": "2.0", "id": shutdown_id, "method": "shutdown", "params": None}
    )
    stdin.write(shutdown.to_bytes())
    stdin.flush()
    shutdown_resp = read_until_response(stdout, shutdown_id)
    assert shutdown_resp.get("id") == shutdown_id, shutdown_resp
    exit_msg = RpcMessage({"jsonrpc": "2.0", "method": "exit", "params": None})
    stdin.write(exit_msg.to_bytes())
    stdin.flush()

    proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"smoke test failed: {e}", file=sys.stderr)
        raise
