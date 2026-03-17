from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast


@dataclass(frozen=True)
class RpcMessage:
    payload: dict[str, object]

    def to_bytes(self) -> bytes:
        body = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body


def read_rpc(stream: IO[bytes]) -> dict[str, object]:
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
    return cast(dict[str, object], json.loads(body.decode("utf-8")))


def read_until_response(stream: IO[bytes], request_id: int) -> dict[str, object]:
    while True:
        msg = read_rpc(stream)
        if msg.get("id") == request_id:
            return msg


def format_request(
    stdin: IO[bytes], stdout: IO[bytes], request_id: int, path: Path
) -> dict[str, object]:
    fmt_req = RpcMessage(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "textDocument/formatting",
            "params": {
                "textDocument": {"uri": path.as_uri()},
                "options": {"tabSize": 2, "insertSpaces": True},
            },
        }
    )
    stdin.write(fmt_req.to_bytes())
    stdin.flush()
    return read_until_response(stdout, request_id)


def main() -> int:
    base = Path(os.environ.get("WORKSPACE", "/tmp/sql-lsp-workspace")).resolve()
    base.mkdir(parents=True, exist_ok=True)

    workspace_a = base / "ws-a"
    workspace_a.mkdir(parents=True, exist_ok=True)
    (workspace_a / ".opencode").mkdir(exist_ok=True)
    cfg_a = {
        "defaultDialect": "starrocks",
        "overrides": {"**\\*.trino.sql": "trino"},
    }
    (workspace_a / ".opencode" / "sql-lsp.json").write_text(
        json.dumps(cfg_a, indent=2), encoding="utf-8"
    )
    trino_file = workspace_a / "test.trino.sql"
    starrocks_file = workspace_a / "test.sql"
    trino_file.write_text("SELECT 1\n", encoding="utf-8")
    starrocks_file.write_text("SELECT 1\n", encoding="utf-8")

    workspace_b = base / "ws-b"
    workspace_b.mkdir(parents=True, exist_ok=True)
    b_file = workspace_b / "missing_config.sql"
    b_file.write_text("SELECT 1\n", encoding="utf-8")

    workspace_c = base / "ws-c"
    workspace_c.mkdir(parents=True, exist_ok=True)
    (workspace_c / ".opencode").mkdir(exist_ok=True)
    (workspace_c / ".opencode" / "sql-lsp.json").write_text(
        "{ this is not valid json }\n", encoding="utf-8"
    )
    c_file = workspace_c / "bad_config.sql"
    c_file.write_text("SELECT 1\n", encoding="utf-8")

    proc = subprocess.Popen(
        [
            "opencode-sql-lsp",
            "--stdio",
            "--workspace",
            str(workspace_a),
        ],
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
                "rootUri": workspace_a.as_uri(),
                "workspaceFolders": [
                    {"uri": workspace_a.as_uri(), "name": "ws-a"},
                    {"uri": workspace_b.as_uri(), "name": "ws-b"},
                    {"uri": workspace_c.as_uri(), "name": "ws-c"},
                ],
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
    did_open(b_file)
    did_open(c_file)

    req_id += 1
    fmt_resp = format_request(stdin, stdout, req_id, trino_file)
    assert fmt_resp.get("id") == req_id, fmt_resp
    assert isinstance(fmt_resp.get("result"), list), fmt_resp

    first_repeat_start = time.perf_counter()
    req_id += 1
    fmt_resp_repeat = format_request(stdin, stdout, req_id, trino_file)
    first_repeat_duration = time.perf_counter() - first_repeat_start
    assert fmt_resp_repeat.get("id") == req_id, fmt_resp_repeat
    assert isinstance(fmt_resp_repeat.get("result"), list), fmt_resp_repeat
    assert first_repeat_duration < 5, first_repeat_duration

    second_repeat_start = time.perf_counter()
    req_id += 1
    fmt_resp_repeat_2 = format_request(stdin, stdout, req_id, trino_file)
    second_repeat_duration = time.perf_counter() - second_repeat_start
    assert fmt_resp_repeat_2.get("id") == req_id, fmt_resp_repeat_2
    assert isinstance(fmt_resp_repeat_2.get("result"), list), fmt_resp_repeat_2
    assert second_repeat_duration < 5, second_repeat_duration

    req_id += 1
    fmt_resp2 = format_request(stdin, stdout, req_id, starrocks_file)
    assert fmt_resp2.get("id") == req_id, fmt_resp2
    assert isinstance(fmt_resp2.get("result"), list), fmt_resp2

    req_id += 1
    fmt_resp3 = format_request(stdin, stdout, req_id, b_file)
    assert fmt_resp3.get("id") == req_id, fmt_resp3
    assert isinstance(fmt_resp3.get("result"), list), fmt_resp3

    req_id += 1
    fmt_resp4 = format_request(stdin, stdout, req_id, c_file)
    assert fmt_resp4.get("id") == req_id, fmt_resp4
    assert isinstance(fmt_resp4.get("result"), list), fmt_resp4

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
