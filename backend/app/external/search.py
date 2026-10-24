import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

"""联网搜索。采集、补搜和补充条件都走 search_web。

用 Tavily 的搜索接口，只要标题、链接和一段摘要。
密钥放在环境变量 TAVILY_API_KEY。
"""

_SEARCH_URL = "https://api.tavily.com/search"


async def search_web(query: str) -> list[dict]:
    """用 Tavily 查网页。没配密钥、查询为空，或请求失败时返回空列表。"""
    query = (query or "").strip()
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not query or not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                _SEARCH_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": 5,
                    "include_answer": False,
                    "country": "china",
                    "language": "zh-cn",
                    "filter_by_language": True,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []
    items = data.get("results") if isinstance(data, dict) else []
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or ""
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        content = item.get("content") or ""
        rows.append({
            "title": (item.get("title") or "")[:120],
            "url": url,
            "snippet": content[:500] if isinstance(content, str) else "",
        })
    return rows
