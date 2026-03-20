from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]


@dataclass(frozen=True)
class RpcMessage:
    payload: JsonObject

    def to_bytes(self) -> bytes:
        body = json.dumps(self.payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        return header + body


def read_rpc(stream: IO[bytes]) -> JsonObject:
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
    return cast(JsonObject, json.loads(body.decode("utf-8")))


def read_until_response(stream: IO[bytes], request_id: int) -> JsonObject:
    while True:
        msg = read_rpc(stream)
        if msg.get("id") == request_id:
            return msg


def read_until_response_collecting(
    stream: IO[bytes], request_id: int, notifications: list[JsonObject]
) -> JsonObject:
    while True:
        msg = read_rpc(stream)
        if msg.get("id") == request_id:
            return msg
        notifications.append(msg)


def read_rpc_with_timeout(stream: IO[bytes], timeout_s: float) -> JsonObject | None:
    ready, _, _ = select.select([stream], [], [], timeout_s)
    if not ready:
        return None
    return read_rpc(stream)


def format_request(
    stdin: IO[bytes],
    stdout: IO[bytes],
    request_id: int,
    path: Path,
    notifications: list[JsonObject],
) -> JsonObject:
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
    _ = stdin.write(fmt_req.to_bytes())
    stdin.flush()
    return read_until_response_collecting(stdout, request_id, notifications)


def request(
    stdin: IO[bytes],
    stdout: IO[bytes],
    request_id: int,
    method: str,
    params: JsonValue,
    notifications: list[JsonObject],
) -> JsonObject:
    message = RpcMessage(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
    )
    _ = stdin.write(message.to_bytes())
    stdin.flush()
    return read_until_response_collecting(stdout, request_id, notifications)


def has_matching_title(items: list[JsonObject], title: str) -> bool:
    return any(item.get("title") == title for item in items)


def has_matching_label(items: list[JsonObject], label: str) -> bool:
    return any(item.get("label") == label for item in items)


def has_skip_diagnostic(diagnostics: list[JsonObject]) -> bool:
    return any(
        diagnostic.get("message") == "Lint skipped (file too large)"
        for diagnostic in diagnostics
    )


def diagnostic_messages(diagnostics: list[JsonObject]) -> list[str]:
    return [
        message
        for diagnostic in diagnostics
        if isinstance((message := diagnostic.get("message")), str)
    ]


def diagnostics_for_uri(
    notifications: list[JsonObject], uri: str
) -> list[list[JsonObject]]:
    batches: list[list[JsonObject]] = []
    for notification in notifications:
        if notification.get("method") != "textDocument/publishDiagnostics":
            continue
        params = notification.get("params")
        if not isinstance(params, dict) or params.get("uri") != uri:
            continue
        diagnostics_raw = params.get("diagnostics")
        if not isinstance(diagnostics_raw, list):
            continue
        batches.append(
            [cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)]
        )
    return batches


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", "opencode_sql_lsp_server.cli"]
    base = Path(os.environ.get("WORKSPACE", "/tmp/sql-lsp-workspace")).resolve()
    base.mkdir(parents=True, exist_ok=True)

    workspace_a = base / "ws-a"
    workspace_a.mkdir(parents=True, exist_ok=True)
    (workspace_a / ".opencode").mkdir(exist_ok=True)
    cfg_a = {
        "defaultDialect": "starrocks",
        "overrides": {"**\\*.trino.sql": "trino"},
        "maxLintLines": 8,
        "maxLintBytes": 512,
    }
    _ = (workspace_a / ".opencode" / "sql-lsp.json").write_text(
        json.dumps(cfg_a, indent=2), encoding="utf-8"
    )
    trino_file = workspace_a / "test.trino.sql"
    starrocks_file = workspace_a / "test.sql"
    invalid_file = workspace_a / "broken.sql"
    lateral_unnest_file = workspace_a / "lateral_unnest.sql"
    json_each_file = workspace_a / "json_each.sql"
    invalid_lateral_unnest_file = workspace_a / "broken_lateral_unnest.sql"
    materialized_view_file = workspace_a / "mv.sql"
    routine_load_file = workspace_a / "routine_load.sql"
    _ = trino_file.write_text("select  1\n", encoding="utf-8")
    _ = starrocks_file.write_text("SELECT 1\n", encoding="utf-8")
    _ = invalid_file.write_text("SELECT * FROM", encoding="utf-8")
    _ = lateral_unnest_file.write_text(
        "SELECT *\nFROM t, LATERAL UNNEST(arr) AS u(x)\n", encoding="utf-8"
    )
    _ = json_each_file.write_text(
        "SELECT *\nFROM t, LATERAL JSON_EACH(j) AS j(k, v)\n",
        encoding="utf-8",
    )
    _ = invalid_lateral_unnest_file.write_text(
        "SELECT *\nFROM t, LATERAL UNNEST(arr) AS u(x)\nWHERE\n",
        encoding="utf-8",
    )
    _ = materialized_view_file.write_text(
        "CREATE MATERIALIZED VIEW mv_sales AS\nSELECT city, SUM(amount) FROM sales GROUP BY city\n",
        encoding="utf-8",
    )
    _ = routine_load_file.write_text(
        'CREATE ROUTINE LOAD load_orders ON orders\nFROM KAFKA\nPROPERTIES("desired_concurrent_number" = "1")\n',
        encoding="utf-8",
    )

    workspace_b = base / "ws-b"
    workspace_b.mkdir(parents=True, exist_ok=True)
    b_file = workspace_b / "missing_config.sql"
    _ = b_file.write_text("SELECT 1\n", encoding="utf-8")

    workspace_c = base / "ws-c"
    workspace_c.mkdir(parents=True, exist_ok=True)
    (workspace_c / ".opencode").mkdir(exist_ok=True)
    _ = (workspace_c / ".opencode" / "sql-lsp.json").write_text(
        "{ this is not valid json }\n", encoding="utf-8"
    )
    c_file = workspace_c / "bad_config.sql"
    _ = c_file.write_text("SELECT 1\n", encoding="utf-8")

    workspace_d = base / "ws-d"
    workspace_d.mkdir(parents=True, exist_ok=True)
    (workspace_d / ".opencode").mkdir(exist_ok=True)
    _ = (workspace_d / ".opencode" / "sql-lsp.json").write_text(
        json.dumps({"maxLintLines": 1, "maxLintBytes": 8}, indent=2),
        encoding="utf-8",
    )
    large_file = workspace_d / "huge.sql"
    _ = large_file.write_text("SELECT\n1\nFROM\nfoo\n", encoding="utf-8")

    child_env = os.environ.copy()
    existing_pythonpath = child_env.get("PYTHONPATH")
    repo_pythonpath = str(repo_root / "src")
    child_env["PYTHONPATH"] = (
        f"{repo_pythonpath}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else repo_pythonpath
    )

    proc = subprocess.Popen(
        [*command, "--stdio", "--workspace", str(workspace_a)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo_root,
        env=child_env,
    )
    if proc.stdin is None or proc.stdout is None:
        raise RuntimeError("Failed to open stdio pipes")
    stdin = proc.stdin
    stdout = proc.stdout
    notifications: list[JsonObject] = []

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
                    {"uri": workspace_d.as_uri(), "name": "ws-d"},
                ],
                "capabilities": {},
            },
        }
    )
    _ = stdin.write(initialize.to_bytes())
    stdin.flush()

    msg = read_until_response_collecting(stdout, req_id, notifications)
    assert msg.get("id") == req_id, msg

    initialized = RpcMessage({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    _ = stdin.write(initialized.to_bytes())
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
        _ = stdin.write(open_msg.to_bytes())
        stdin.flush()

    did_open(trino_file)
    did_open(starrocks_file)
    did_open(invalid_file)
    did_open(lateral_unnest_file)
    did_open(json_each_file)
    did_open(invalid_lateral_unnest_file)
    did_open(materialized_view_file)
    did_open(routine_load_file)
    did_open(b_file)
    did_open(c_file)
    did_open(large_file)

    req_id += 1
    completion_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/completion",
        {
            "textDocument": {"uri": trino_file.as_uri()},
            "position": {"line": 0, "character": 0},
        },
        notifications,
    )
    assert completion_resp.get("id") == req_id, completion_resp
    completion_items_raw = completion_resp.get("result")
    assert isinstance(completion_items_raw, list), completion_resp
    completion_items = [
        cast(JsonObject, item)
        for item in completion_items_raw
        if isinstance(item, dict)
    ]
    assert has_matching_label(completion_items, "SELECT"), completion_items
    assert has_matching_label(completion_items, "CREATE MATERIALIZED VIEW"), (
        completion_items
    )
    assert has_matching_label(completion_items, "CREATE ROUTINE LOAD"), completion_items

    req_id += 1
    hover_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/hover",
        {
            "textDocument": {"uri": trino_file.as_uri()},
            "position": {"line": 0, "character": 1},
        },
        notifications,
    )
    assert hover_resp.get("id") == req_id, hover_resp
    hover_result = hover_resp.get("result")
    assert isinstance(hover_result, dict), hover_resp

    req_id += 1
    hover_starrocks_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/hover",
        {
            "textDocument": {"uri": materialized_view_file.as_uri()},
            "position": {"line": 0, "character": 10},
        },
        notifications,
    )
    assert hover_starrocks_resp.get("id") == req_id, hover_starrocks_resp
    hover_starrocks_result = hover_starrocks_resp.get("result")
    assert isinstance(hover_starrocks_result, dict), hover_starrocks_resp
    hover_starrocks_contents = hover_starrocks_result.get("contents")
    assert isinstance(hover_starrocks_contents, dict), hover_starrocks_result
    hover_starrocks_value = hover_starrocks_contents.get("value")
    assert isinstance(hover_starrocks_value, str), hover_starrocks_contents
    assert "CREATE MATERIALIZED VIEW" in hover_starrocks_value

    req_id += 1
    symbol_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/documentSymbol",
        {"textDocument": {"uri": trino_file.as_uri()}},
        notifications,
    )
    assert symbol_resp.get("id") == req_id, symbol_resp
    symbol_result = symbol_resp.get("result")
    assert isinstance(symbol_result, list), symbol_resp
    assert symbol_result, symbol_resp

    req_id += 1
    starrocks_symbol_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/documentSymbol",
        {"textDocument": {"uri": materialized_view_file.as_uri()}},
        notifications,
    )
    assert starrocks_symbol_resp.get("id") == req_id, starrocks_symbol_resp
    starrocks_symbol_result = starrocks_symbol_resp.get("result")
    assert isinstance(starrocks_symbol_result, list), starrocks_symbol_resp
    assert any(
        isinstance(item, dict) and item.get("name") == "Materialized view mv_sales"
        for item in starrocks_symbol_result
    ), starrocks_symbol_result

    req_id += 1
    workspace_symbol_resp = request(
        stdin,
        stdout,
        req_id,
        "workspace/symbol",
        {"query": "SELECT"},
        notifications,
    )
    assert workspace_symbol_resp.get("id") == req_id, workspace_symbol_resp
    workspace_symbol_result = workspace_symbol_resp.get("result")
    assert isinstance(workspace_symbol_result, list), workspace_symbol_resp
    assert workspace_symbol_result, workspace_symbol_resp

    req_id += 1
    workspace_symbol_starrocks_resp = request(
        stdin,
        stdout,
        req_id,
        "workspace/symbol",
        {"query": "mv_sales"},
        notifications,
    )
    assert workspace_symbol_starrocks_resp.get("id") == req_id, (
        workspace_symbol_starrocks_resp
    )
    workspace_symbol_starrocks_result = workspace_symbol_starrocks_resp.get("result")
    assert isinstance(workspace_symbol_starrocks_result, list), (
        workspace_symbol_starrocks_resp
    )
    assert any(
        isinstance(item, dict) and item.get("name") == "Materialized view mv_sales"
        for item in workspace_symbol_starrocks_result
    ), workspace_symbol_starrocks_result

    req_id += 1
    code_action_resp = request(
        stdin,
        stdout,
        req_id,
        "textDocument/codeAction",
        {
            "textDocument": {"uri": trino_file.as_uri()},
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 6},
            },
            "context": {"diagnostics": []},
        },
        notifications,
    )
    assert code_action_resp.get("id") == req_id, code_action_resp
    code_actions_raw = code_action_resp.get("result")
    assert isinstance(code_actions_raw, list), code_action_resp
    code_actions = [
        cast(JsonObject, action)
        for action in code_actions_raw
        if isinstance(action, dict)
    ]
    assert has_matching_title(code_actions, "Format with sqlfluff"), code_actions

    req_id += 1
    fmt_resp = format_request(stdin, stdout, req_id, trino_file, notifications)
    assert fmt_resp.get("id") == req_id, fmt_resp
    assert isinstance(fmt_resp.get("result"), list), fmt_resp

    first_repeat_start = time.perf_counter()
    req_id += 1
    fmt_resp_repeat = format_request(stdin, stdout, req_id, trino_file, notifications)
    first_repeat_duration = time.perf_counter() - first_repeat_start
    assert fmt_resp_repeat.get("id") == req_id, fmt_resp_repeat
    assert isinstance(fmt_resp_repeat.get("result"), list), fmt_resp_repeat
    assert first_repeat_duration < 5, first_repeat_duration

    second_repeat_start = time.perf_counter()
    req_id += 1
    fmt_resp_repeat_2 = format_request(stdin, stdout, req_id, trino_file, notifications)
    second_repeat_duration = time.perf_counter() - second_repeat_start
    assert fmt_resp_repeat_2.get("id") == req_id, fmt_resp_repeat_2
    assert isinstance(fmt_resp_repeat_2.get("result"), list), fmt_resp_repeat_2
    assert second_repeat_duration < 5, second_repeat_duration

    req_id += 1
    fmt_resp2 = format_request(stdin, stdout, req_id, starrocks_file, notifications)
    assert fmt_resp2.get("id") == req_id, fmt_resp2
    assert isinstance(fmt_resp2.get("result"), list), fmt_resp2

    req_id += 1
    fmt_resp3 = format_request(stdin, stdout, req_id, b_file, notifications)
    assert fmt_resp3.get("id") == req_id, fmt_resp3
    assert isinstance(fmt_resp3.get("result"), list), fmt_resp3

    req_id += 1
    fmt_resp4 = format_request(stdin, stdout, req_id, c_file, notifications)
    assert fmt_resp4.get("id") == req_id, fmt_resp4
    assert isinstance(fmt_resp4.get("result"), list), fmt_resp4

    skipped_message_seen = False
    invalid_diagnostic_messages: list[str] = []
    lateral_unnest_diagnostic_messages: list[str] = []
    json_each_diagnostic_messages: list[str] = []
    invalid_lateral_unnest_messages: list[str] = []
    for diagnostics in diagnostics_for_uri(notifications, invalid_file.as_uri()):
        invalid_diagnostic_messages.extend(diagnostic_messages(diagnostics))
    for diagnostics in diagnostics_for_uri(notifications, lateral_unnest_file.as_uri()):
        lateral_unnest_diagnostic_messages.extend(diagnostic_messages(diagnostics))
    for diagnostics in diagnostics_for_uri(notifications, json_each_file.as_uri()):
        json_each_diagnostic_messages.extend(diagnostic_messages(diagnostics))
    for diagnostics in diagnostics_for_uri(
        notifications, invalid_lateral_unnest_file.as_uri()
    ):
        invalid_lateral_unnest_messages.extend(diagnostic_messages(diagnostics))
    for diagnostics in diagnostics_for_uri(notifications, large_file.as_uri()):
        if has_skip_diagnostic(diagnostics):
            skipped_message_seen = True
            break

    for _ in range(12):
        maybe_diag = read_rpc_with_timeout(stdout, 0.5)
        if maybe_diag is None:
            break
        if maybe_diag.get("method") != "textDocument/publishDiagnostics":
            continue
        params = maybe_diag.get("params")
        if not isinstance(params, dict):
            continue
        if params.get("uri") == invalid_file.as_uri():
            diagnostics_raw = params.get("diagnostics")
            if isinstance(diagnostics_raw, list):
                diagnostics = [
                    cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)
                ]
                invalid_diagnostic_messages.extend(diagnostic_messages(diagnostics))
        if params.get("uri") == lateral_unnest_file.as_uri():
            diagnostics_raw = params.get("diagnostics")
            if isinstance(diagnostics_raw, list):
                diagnostics = [
                    cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)
                ]
                lateral_unnest_diagnostic_messages.extend(
                    diagnostic_messages(diagnostics)
                )
        if params.get("uri") == json_each_file.as_uri():
            diagnostics_raw = params.get("diagnostics")
            if isinstance(diagnostics_raw, list):
                diagnostics = [
                    cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)
                ]
                json_each_diagnostic_messages.extend(diagnostic_messages(diagnostics))
        if params.get("uri") == invalid_lateral_unnest_file.as_uri():
            diagnostics_raw = params.get("diagnostics")
            if isinstance(diagnostics_raw, list):
                diagnostics = [
                    cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)
                ]
                invalid_lateral_unnest_messages.extend(diagnostic_messages(diagnostics))
        if params.get("uri") != large_file.as_uri():
            continue
        diagnostics_raw = params.get("diagnostics")
        if not isinstance(diagnostics_raw, list):
            continue
        diagnostics = [
            cast(JsonObject, d) for d in diagnostics_raw if isinstance(d, dict)
        ]
        if has_skip_diagnostic(diagnostics):
            skipped_message_seen = True
        if skipped_message_seen:
            break
    assert skipped_message_seen
    assert invalid_diagnostic_messages, notifications
    assert all(
        "<bound method" not in message for message in invalid_diagnostic_messages
    ), invalid_diagnostic_messages
    assert all(
        "[PRS]" not in message for message in lateral_unnest_diagnostic_messages
    ), lateral_unnest_diagnostic_messages
    assert all("[PRS]" not in message for message in json_each_diagnostic_messages), (
        json_each_diagnostic_messages
    )
    assert any("[PRS]" in message for message in invalid_lateral_unnest_messages), (
        invalid_lateral_unnest_messages
    )

    shutdown_id = req_id + 1
    shutdown = RpcMessage(
        {"jsonrpc": "2.0", "id": shutdown_id, "method": "shutdown", "params": None}
    )
    _ = stdin.write(shutdown.to_bytes())
    stdin.flush()
    shutdown_resp = read_until_response(stdout, shutdown_id)
    assert shutdown_resp.get("id") == shutdown_id, shutdown_resp
    exit_msg = RpcMessage({"jsonrpc": "2.0", "method": "exit", "params": None})
    _ = stdin.write(exit_msg.to_bytes())
    stdin.flush()

    _ = proc.wait(timeout=10)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"smoke test failed: {e}", file=sys.stderr)
        raise
