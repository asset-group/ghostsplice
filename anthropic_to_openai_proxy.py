"""
Minimal proxy: accepts Anthropic Messages API, forwards to OpenAI via Codex proxy.
Claude Code sends requests here thinking it's talking to Anthropic.
Responses are translated back to Anthropic format.

Usage:
  python anthropic_to_openai_proxy.py
  Then: ANTHROPIC_BASE_URL=http://127.0.0.1:4000 claude
"""

from flask import Flask, request, jsonify
import requests
import json

app = Flask(__name__)

import os
BACKEND = os.environ.get("PROXY_BACKEND", "gpt54")

BACKENDS = {
    "gpt54": {
        "base": "http://localhost:9998/v1",
        "model": "gpt-5.4",
        "key": "dummy",
    },
    "gemini": {
        "base": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.5-flash",
        "key": os.environ.get("OPENROUTER_API_KEY", ""),
    },
    "gemini31": {
        "base": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.5-pro",
        "key": os.environ.get("OPENROUTER_API_KEY", ""),
    },
}

cfg = BACKENDS.get(BACKEND, BACKENDS["gpt54"])
OPENAI_BASE = cfg["base"]
OPENAI_MODEL = cfg["model"]
OPENAI_KEY = cfg["key"]

@app.route("/v1/messages", methods=["POST"])
def messages():
    data = request.json

    # Convert Anthropic messages to OpenAI format
    openai_messages = []

    # System prompt
    if "system" in data:
        sys_text = data["system"]
        if isinstance(sys_text, list):
            sys_text = " ".join(b.get("text", "") for b in sys_text)
        openai_messages.append({"role": "system", "content": sys_text})

    # Messages
    for msg in data.get("messages", []):
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, list):
            # Handle content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block["text"])
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    text_parts.append(json.dumps(block.get("content", "")))
                elif isinstance(block, dict) and block.get("type") == "tool_use":
                    text_parts.append(json.dumps({"tool": block.get("name"), "input": block.get("input")}))
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)
        openai_messages.append({"role": role, "content": content})

    # Handle tools
    openai_tools = None
    if "tools" in data:
        openai_tools = []
        for tool in data["tools"]:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                }
            })

    # Forward to OpenAI
    openai_payload = {
        "model": OPENAI_MODEL,
        "messages": openai_messages,
        "max_tokens": data.get("max_tokens", 4096),
        "temperature": data.get("temperature", 0),
    }
    if openai_tools:
        openai_payload["tools"] = openai_tools

    try:
        resp = requests.post(
            f"{OPENAI_BASE}/chat/completions",
            json=openai_payload,
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            timeout=120,
        )
        openai_resp = resp.json()
    except Exception as e:
        return jsonify({"error": {"type": "api_error", "message": str(e)}}), 500

    # Convert OpenAI response to Anthropic format
    choice = openai_resp.get("choices", [{}])[0]
    message = choice.get("message", {})

    content_blocks = []

    # Handle tool calls
    if message.get("tool_calls"):
        for tc in message["tool_calls"]:
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except:
                args = {}
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", "toolu_proxy"),
                "name": func.get("name", ""),
                "input": args,
            })

    # Handle text content
    if message.get("content"):
        content_blocks.append({
            "type": "text",
            "text": message["content"],
        })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop_reason = "end_turn"
    if choice.get("finish_reason") == "tool_calls":
        stop_reason = "tool_use"
    elif choice.get("finish_reason") == "length":
        stop_reason = "max_tokens"

    anthropic_resp = {
        "id": openai_resp.get("id", "msg_proxy"),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": data.get("model", "claude-sonnet-4-20250514"),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": openai_resp.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": openai_resp.get("usage", {}).get("completion_tokens", 0),
        },
    }

    return jsonify(anthropic_resp)

@app.route("/v1/models", methods=["GET"])
def models():
    return jsonify({
        "data": [
            {"id": "claude-sonnet-4-20250514", "type": "model"},
            {"id": "claude-haiku-4-5-20251001", "type": "model"},
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "backend": OPENAI_MODEL, "profile": BACKEND})

if __name__ == "__main__":
    print(f"Anthropic->OpenAI proxy starting on :4000")
    print(f"Backend: {OPENAI_BASE} model={OPENAI_MODEL}")
    print(f"Usage: ANTHROPIC_BASE_URL=http://127.0.0.1:4000 claude")
    app.run(host="127.0.0.1", port=4000, debug=False)
