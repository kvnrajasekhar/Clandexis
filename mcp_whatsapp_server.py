#!/usr/bin/env python3
"""
Lightweight Zero-Dependency MCP Server for Meta WhatsApp Business Cloud API.
Communicates via standard JSON-RPC 2.0 over stdin/stdout.
Uses credentials from invisible-erp/.env.
"""

import sys
import os
import json
import urllib.request
import urllib.error

# Load environment variables from .env
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ENV = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip("\"' \t")

TOKEN = os.getenv("META_WHATSAPP_TOKEN", ENV.get("META_WHATSAPP_TOKEN", ""))
PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", ENV.get("META_PHONE_NUMBER_ID", ""))
API_VERSION = os.getenv("META_API_VERSION", ENV.get("META_API_VERSION", "v21.0"))
BASE_URL = f"https://graph.facebook.com/{API_VERSION}"

def graph_request(endpoint: str, data: dict = None, method: str = "GET") -> dict:
    url = f"{BASE_URL}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    }
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        try:
            return {"error": True, "code": e.code, "details": json.loads(err_body)}
        except Exception:
            return {"error": True, "code": e.code, "details": err_body}
    except Exception as ex:
        return {"error": True, "details": str(ex)}

TOOLS = [
    {
        "name": "whatsapp_get_phone_status",
        "description": "Fetch verified name, quality rating, and status for the configured WhatsApp Business phone number.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "whatsapp_send_text_message",
        "description": "Send a WhatsApp text message to an E.164 phone number (e.g. +919876543210).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient phone number with country code (e.g. +919876543210 or 919876543210)"
                },
                "text": {
                    "type": "string",
                    "description": "The message body text to send."
                }
            },
            "required": ["to", "text"]
        }
    },
    {
        "name": "whatsapp_send_interactive_buttons",
        "description": "Send interactive quick-reply confirmation buttons (up to 3 buttons) to a recipient.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Recipient phone number with country code"
                },
                "body_text": {
                    "type": "string",
                    "description": "Main prompt text explaining the decision/choice"
                },
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Unique button ID callback"},
                            "title": {"type": "string", "description": "Button label (max 20 chars)"}
                        },
                        "required": ["id", "title"]
                    },
                    "description": "Array of 1 to 3 buttons"
                },
                "header_text": {
                    "type": "string",
                    "description": "Optional bold header title"
                }
            },
            "required": ["to", "body_text", "buttons"]
        }
    }
]

def handle_call_tool(name: str, arguments: dict) -> dict:
    recipient = (arguments.get("to") or "").replace("+", "").replace(" ", "").replace("-", "")

    if name == "whatsapp_get_phone_status":
        res = graph_request(PHONE_NUMBER_ID)
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}

    elif name == "whatsapp_send_text_message":
        text = arguments.get("text", "")
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"body": text}
        }
        res = graph_request(f"{PHONE_NUMBER_ID}/messages", data=payload, method="POST")
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}

    elif name == "whatsapp_send_interactive_buttons":
        body_text = arguments.get("body_text", "")
        buttons = arguments.get("buttons", [])[:3]
        header = arguments.get("header_text")

        meta_buttons = [
            {
                "type": "reply",
                "reply": {
                    "id": b.get("id", f"btn_{i}"),
                    "title": (b.get("title") or "")[:20]
                }
            }
            for i, b in enumerate(buttons)
        ]

        interactive = {
            "type": "button",
            "body": {"text": body_text},
            "footer": {"text": "Clandexis Invisible ERP"},
            "action": {"buttons": meta_buttons}
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "interactive",
            "interactive": interactive
        }
        res = graph_request(f"{PHONE_NUMBER_ID}/messages", data=payload, method="POST")
        return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}

    return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        msg_id = req.get("id")

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "whatsapp-cloud-api", "version": "1.0.0"}
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS}
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            args = params.get("arguments", {})
            call_res = handle_call_tool(tool_name, args)
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": call_res
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "ping":
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        else:
            if msg_id is not None:
                res = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method {method} not found"}
                }
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()

if __name__ == "__main__":
    main()
