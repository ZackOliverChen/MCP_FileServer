# mcp_proxy.py - A proxy for forwarding JSON-RPC requests to the MCP server.
# Since the server only accepts requests via HTTP, this proxy forwards JSON-RPC requests from stdin to the server.
import json
import sys
import requests

# target must be extracted from the service configuration file
# It should be set according to the MCP server's address and port.
TARGET_URL = "http://127.0.0.1:8123/mcp"
SESSION_ID_HEADER = "mcp-session-id"
PROTO_VERSION_HEADER = "MCP-Protocol-Version"

_session_id = None


def _sanitize_payload(payload):
    """Ensures responses adhere strictly to the camelCase inputSchema protocol."""
    if not isinstance(payload, dict):
        return payload

    if "result" in payload and isinstance(payload["result"], dict) and "tools" in payload["result"]:
        for tool in payload["result"]["tools"]:
            if not isinstance(tool, dict):
                continue
            if "input_schema" in tool and "inputSchema" not in tool:
                tool["inputSchema"] = tool.pop("input_schema")
            if "inputSchema" not in tool or tool["inputSchema"] is None:
                tool["inputSchema"] = {"type": "object", "properties": {}}
                
    return payload


def _emit_json(payload):
    sanitized_payload = _sanitize_payload(payload)
    sys.stdout.write(json.dumps(sanitized_payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _parse_sse_payload(response_text: str):
    messages = []
    current = {}

    for line in response_text.splitlines():
        if not line:
            if current.get("data"):
                messages.append(current)
                current = {}
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip() # <-- Fix [1].strip()
        elif line.startswith("data:"):
            value = line.split(":", 1)[1].strip()            # <-- Fix [1].strip()
        elif line.startswith("id:"):
            current["id"] = line.split(":", 1)[1].strip()              # <-- Fix [1].strip()
        elif line.startswith("retry:"):
            current["retry"] = line.split(":", 1)[1].strip()           # <-- Fix [1].strip()

    if current.get("data"):
        messages.append(current)

    parsed_messages = []
    for msg in messages:
        payload = msg.get("data")
        if not payload:
            continue
        try:
            parsed_messages.append(json.loads(payload))
        except json.JSONDecodeError:
            parsed_messages.append({"text": payload})
    return parsed_messages


def _forward_jsonrpc(raw_line: str, message_id=None):
    global _session_id

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if _session_id:
        headers[SESSION_ID_HEADER] = _session_id

    try:
        response = requests.post(
            TARGET_URL,
            data=raw_line,
            headers=headers,
            timeout=30,
            stream=True,
        )
    except requests.RequestException as exc:
        _emit_json({"jsonrpc": "2.0", "id": message_id, "error": {"code": -32000, "message": f"Proxy Error: {exc}"}})
        return

    if response.status_code >= 400:
        _emit_json({
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {
                "code": response.status_code,
                "message": response.text,
            },
        })
        return

    new_session_id = response.headers.get(SESSION_ID_HEADER)
    if new_session_id:
        _session_id = new_session_id

    content_type = response.headers.get("Content-Type", "").lower()
    body = response.text.strip()

    if "text/event-stream" in content_type:
        parsed_messages = _parse_sse_payload(body)
        if not parsed_messages:
            _emit_json({"jsonrpc": "2.0", "id": message_id, "result": {"status": "ok"}})
            return

        for item in parsed_messages:
            _emit_json(item)
        return

    if body:
        try:
            payload = json.loads(body)
            # Ensure we maintain the request's ID tracking if the server returns null or omits it
            if isinstance(payload, dict) and payload.get("id") is None and message_id is not None:
                payload["id"] = message_id
            _emit_json(payload)
            return
        except json.JSONDecodeError:
            pass

    _emit_json({"jsonrpc": "2.0", "id": message_id, "result": {"status": "ok"}})


def main():
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            payload = json.loads(line)
            message_id = None
            if isinstance(payload, dict):
                message_id = payload.get("id")
                
            _forward_jsonrpc(line, message_id=message_id)
        except Exception as exc:
            _emit_json({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": f"Proxy parse error: {exc}",
                },
            })


if __name__ == "__main__":
    main()
