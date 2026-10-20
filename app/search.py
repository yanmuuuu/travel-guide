import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# 基础搜索，只要摘要，比抓整页正文快
_SEARCH_URL = (
    "{host}/v3/openapi/workspaces/default/web-search/ops-web-search-001"
)


def search_web(query: str) -> list[dict]:
    """用阿里云联网搜索查网页。失败时返回空列表，图会走到道歉。"""
    api_key = os.getenv("ALIYUN_SEARCH_API_KEY", "").strip()
    host = os.getenv("ALIYUN_SEARCH_HOST", "").strip().rstrip("/")
    if not api_key or not host or not (query or "").strip():
        return []
    if not host.startswith("http"):
        host = "https://" + host
    try:
        response = httpx.post(
            _SEARCH_URL.format(host=host),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "query": query.strip(),
                "query_rewrite": False,
                "top_k": 4,
                "content_type": "snippet",
                "way": "lite",
            },
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    result = data.get("result") if isinstance(data, dict) else None
    items = result.get("search_result") if isinstance(result, dict) else []
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("link") or ""
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        snippet = item.get("snippet") or item.get("content") or ""
        rows.append({
            "title": (item.get("title") or "")[:120],
            "url": url,
            "snippet": snippet[:500] if isinstance(snippet, str) else "",
        })
    return rows
