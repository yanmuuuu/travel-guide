import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

"""小红书笔记。采集和补充搜索各取最多 2 篇。

地址放在 XHS_MCP_URL。没配、没登录或超时，返回空列表，不挡住网页搜索。
只调用搜索和笔记详情，不发帖。
"""

_READONLY = ("search_feeds", "get_feed_detail")


class _Session:
    """直接按这个服务的 initialize 来调。不走系统代理，避免梯子把本机地址收成 502。"""

    def __init__(self, url: str):
        self.url = url
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, read=90), trust_env=False)
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

    async def __aenter__(self):
        response = await self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "travel-guide", "version": "0.1"},
            },
        })
        response.raise_for_status()
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self.headers["Mcp-Session-Id"] = session_id
        await self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()

    async def call_tool(self, name: str, arguments: dict):
        if name not in _READONLY:
            raise RuntimeError(f"xhs tool not allowed: {name}")
        response = await self.client.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            message = payload["error"].get("message") or "xiaohongshu mcp error"
            raise RuntimeError(message)
        blocks = []
        for block in (payload.get("result") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                blocks.append(SimpleNamespace(text=block.get("text") or ""))
        return SimpleNamespace(data=None, content=blocks)


async def search_notes(query: str) -> list[dict]:
    """搜最多 2 篇笔记，收成标题、链接、摘要。"""
    query = (query or "").strip()
    url = os.getenv("XHS_MCP_URL", "").strip()
    if not query or not url:
        return []
    notes = []
    try:
        async with _Session(url) as client:
            found = await client.call_tool("search_feeds", {"keyword": query})
            feeds = _feeds(found)[:2]
            for feed in feeds:
                detail = await _detail(client, feed)
                title = (detail or {}).get("title") or feed.get("title") or ""
                snippet = (detail or {}).get("desc") or title
                note_id = feed.get("feed_id") or ""
                if not title or not note_id:
                    continue
                notes.append({
                    "title": f"小红书｜{title[:40]}",
                    "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                    "snippet": snippet[:500] if isinstance(snippet, str) else "",
                })
    except Exception:
        return notes
    return notes


async def _detail(client, feed: dict) -> dict | None:
    feed_id = feed.get("feed_id") or ""
    token = feed.get("xsec_token") or ""
    if not feed_id or not token:
        return None
    try:
        result = await client.call_tool("get_feed_detail", {
            "feed_id": feed_id,
            "xsec_token": token,
        })
    except Exception:
        return None
    data = _json_text(result)
    if not isinstance(data, dict):
        return None
    note = ((data.get("data") or {}).get("note")) or data.get("note") or {}
    if not isinstance(note, dict):
        return None
    return {
        "title": (note.get("title") or "").strip(),
        "desc": (note.get("desc") or "").strip(),
    }


def _feeds(result) -> list[dict]:
    data = _json_text(result)
    if not isinstance(data, dict):
        return []
    rows = data.get("feeds") or data.get("data") or []
    feeds = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        feed_id = row.get("id") or row.get("feed_id") or ""
        token = row.get("xsecToken") or row.get("xsec_token") or ""
        card = row.get("noteCard") if isinstance(row.get("noteCard"), dict) else {}
        if not card and isinstance(row.get("note_card"), dict):
            card = row.get("note_card")
        title = row.get("title") or card.get("displayTitle") or card.get("display_title") or ""
        if feed_id and token:
            feeds.append({
                "feed_id": feed_id,
                "xsec_token": token,
                "title": title,
            })
    return feeds


def _json_text(result) -> dict | list | None:
    data = getattr(result, "data", None)
    if isinstance(data, (dict, list)):
        return data
    texts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", "")
        if isinstance(text, str) and text.strip():
            texts.append(text)
    raw = "\n".join(texts).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            return None
