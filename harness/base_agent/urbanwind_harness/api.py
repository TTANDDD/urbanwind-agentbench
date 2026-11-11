from __future__ import annotations

import json
import http.client
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


class APIError(RuntimeError):
    def __init__(self, status, message, response=None, attempts=None):
        super().__init__(re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)[:1200])
        self.status = status
        self.response, self.attempts = response, attempts or []


class PoeClient:
    def __init__(self, key_path: Path, model="deepseek-v4.1-flash",
                 base_url="https://api.poe.com/v1", timeout=600, retries=3, relay_url=None):
        self.key_path, self.model = key_path, model
        self.base_url = base_url.rstrip("/").removesuffix("/models")
        if self.base_url != "https://api.poe.com/v1":
            raise ValueError("Poe credentials require the official HTTPS origin")
        if relay_url is not None and not re.fullmatch(r"http://127\.0\.0\.1:\d+/v1", relay_url):
            raise ValueError("relay_must_be_local_loopback_over_SSH")
        self.transport_url = relay_url or self.base_url
        self.timeout, self.retries = timeout, retries
        self.deadline = None

    def complete(self, messages, max_tokens=None, tools=None):
        body = {"model": self.model, "messages": messages,
                "stream": False}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools is not None:
            body.update({"tools": tools, "tool_choice": "auto", "parallel_tool_calls": False})
        attempts = []
        for attempt in range(self.retries):
            started = time.monotonic()
            remaining = self.timeout if self.deadline is None else self.deadline-started
            if remaining <= 0:
                raise APIError(None, "active_time_budget_during_api", attempts=attempts)
            request = urllib.request.Request(self.transport_url + "/chat/completions",
                data=json.dumps(body).encode(), method="POST", headers={
                    "Authorization": "Bearer " + self.key_path.read_text().strip(),
                    "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=min(self.timeout, remaining)) as response:
                    data = json.load(response)
                if data.get("error"):
                    raise APIError(200, str(data["error"]))
                choice = data.get("choices", [{}])[0]
                if choice.get("finish_reason") in ("length", "max_tokens"):
                    raise APIError(200, "truncated_response_not_executed", data)
                content = choice.get("message", {}).get("content")
                tool_calls = choice.get("message", {}).get("tool_calls")
                if not tool_calls and (not isinstance(content, str) or not content.strip()):
                    raise APIError(200, "empty_assistant_response")
                attempts.append({"status": 200, "latency_s": time.monotonic()-started,
                                 "usage": data.get("usage", {})})
                return {"content": content or "", "message": choice.get("message", {}), "usage": data.get("usage", {}),
                        "model": data.get("model"), "finish_reason": choice.get("finish_reason"),
                        "provider_response": data,
                        "attempts": attempts}
            except urllib.error.HTTPError as exc:
                message = exc.read(4096).decode(errors="replace")
                error = APIError(exc.code, message)
                attempts.append({"status": exc.code, "latency_s": time.monotonic()-started})
                if exc.code not in (429, 500, 502, 503, 504):
                    error.attempts = attempts
                    raise error
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    http.client.HTTPException, json.JSONDecodeError, UnicodeDecodeError) as exc:
                error = APIError(None, type(exc).__name__)
                attempts.append({"status": "transport_error", "error_type": type(exc).__name__,
                                 "latency_s": time.monotonic()-started, "request_usage": "unknown"})
            except APIError as exc:
                error = exc
                attempts.append({"status": "invalid_response", "latency_s": time.monotonic()-started})
                # Never execute or silently replace a truncated action.
                if exc.response is not None:
                    error.attempts = attempts
                    raise error
            if attempt + 1 < self.retries:
                time.sleep(min(2**attempt, 8))
        error.attempts = attempts
        raise error


class DeepSeekClient(PoeClient):
    """Same bounded completion contract, with a distinct official credential origin."""
    def __init__(self, key_path: Path, model="deepseek-flash",
                 base_url="https://api.deepseek.com", timeout=600, retries=3, relay_url=None):
        if base_url.rstrip("/") != "https://api.deepseek.com":
            raise ValueError("DeepSeek credentials require the official HTTPS origin")
        if relay_url is not None:
            raise ValueError("DeepSeek migration uses direct official HTTPS; no Poe relay")
        self.key_path, self.model = key_path, model
        self.base_url = self.transport_url = "https://api.deepseek.com"
        self.timeout, self.retries = timeout, retries
        self.deadline = None


def action_from_text(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1])
    action = json.loads(stripped)
    if not isinstance(action, dict) or action.get("action") not in ("shell", "run", "finish", "inspect", "recover"):
        raise ValueError("invalid_action")
    if action["action"] in ("shell", "run") and (not isinstance(action.get("command"), str)
                                           or not action["command"].strip()):
        raise ValueError("shell_command_required")
    if action["action"] == "run" and action.get("stage") not in ("mesh", "solve", "postprocess", "deliver"):
        raise ValueError("run_stage_required")
    return action
