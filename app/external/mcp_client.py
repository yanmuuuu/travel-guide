import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

"""几路远程服务共用的 HTTP 客户端。这个文件本身不提供火车票、酒店或景点数据。

和小红书一样：先 initialize，再 tools/call。
不走系统代理，避免梯子把国内地址收成 502。
返回的正文如果是 JSON 就解开；解不开就留下原文。
"""


class Session:
    """一条 MCP HTTP 会话。只允许调用方点名的工具。"""

    def __init__(self, url: str, allowed: tuple[str, ...], headers: dict | None = None):
        self.url = url
        self.allowed = allowed
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=60), trust_env=False)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if headers:
            self.headers.update(headers)
        self._next_id = 1

    async def __aenter__(self):
        await self._post("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "travel-guide", "version": "0.1"},
        })
        await self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()

    async def call(self, name: str, arguments: dict):
        if name not in self.allowed:
            raise RuntimeError(f"mcp tool not allowed: {name}")
        payload = await self._post("tools/call", {"name": name, "arguments": arguments})
        result = payload.get("result") or {}
        if result.get("isError"):
            message = _text_blocks(result) or "mcp tool error"
            raise RuntimeError(message)
        raw = _text_blocks(result)
        parsed = _json_text(raw)
        if parsed is not None:
            return parsed
        structured = result.get("structuredContent")
        if isinstance(structured, (dict, list)):
            return structured
        # 有的工具只回一段文字，例如高德专属地图的链接。原样交回，别当成空。
        return raw or None

    async def _post(self, method: str, params: dict) -> dict:
        call_id = self._next_id
        self._next_id += 1
        response = await self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0",
            "id": call_id,
            "method": method,
            "params": params,
        })
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        payload = _payload(response)
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            raise RuntimeError(error.get("message") or "mcp error")
        return payload if isinstance(payload, dict) else {}


def env(name: str) -> str:
    """读一项密钥或地址。前后空白去掉，没有就是空字符串。"""
    return os.getenv(name, "").strip()


def iso_day(value: str) -> str:
    """收成 YYYY-MM-DD。已经是这个格式就用。只有月日时，在今天起 40 天里对上。"""
    text = (value or "").strip()
    pieces = text.split("-")
    if len(pieces) == 3 and all(piece.isdigit() for piece in pieces):
        current = _make_date(int(pieces[0]), int(pieces[1]), int(pieces[2]))
        return current.isoformat() if current else ""
    if text.endswith("日") and "月" in text:
        month_text, day_text = text[:-1].split("月", 1)
        if month_text.isdigit() and day_text.isdigit():
            current = _resolve_month_day(int(month_text), int(day_text))
            return current.isoformat() if current else ""
    return ""


def rows(data, *keys: str) -> list[dict]:
    """从常见的包一层里取出字典列表。没有就返回空。"""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            found = rows(value, *keys)
            if found:
                return found
    for value in data.values():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return list(value)
        if isinstance(value, dict):
            found = rows(value, *keys)
            if found:
                return found
    return []


def _resolve_month_day(month: int, day: int) -> date | None:
    today = date.today()
    for offset in range(40):
        current = today + timedelta(days=offset)
        if current.month == month and current.day == day:
            return current
    return None


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _payload(response: httpx.Response) -> dict:
    text = response.text.strip()
    if not text:
        return {}
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type or text.startswith("event:") or text.startswith("data:"):
        last = {}
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                last = parsed
        return last
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text_blocks(result: dict) -> str:
    texts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text") or ""
            if isinstance(text, str) and text.strip():
                texts.append(text)
    return "\n".join(texts).strip()


def _json_text(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            start = raw.find("[")
            end = raw.rfind("]")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
