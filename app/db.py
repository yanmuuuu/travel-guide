import os
from pathlib import Path

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

"""对话存档、长期偏好和终稿放在哪。

配了 DATABASE_URL 就用 Postgres，LangGraph 第一次连上会在 DATABASE_SCHEMA 里自己建存档表和记忆表。
没配就放内存，进程一关就没了。
终稿写进 public.travel_guide，这张表要事先建好。
"""

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
# 存档表和记忆表单独放一个模式，不和库里别的 LangGraph 服务混在一起。
DATABASE_SCHEMA = os.getenv("DATABASE_SCHEMA", "travel").strip() or "travel"

_pool = None


def _connections():
    """整个进程共用一个连接池。"""
    global _pool
    if _pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            DATABASE_URL,
            max_size=10,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": f"-c search_path={DATABASE_SCHEMA}",
            },
            open=True,
        )
        with _pool.connection() as conn:
            conn.execute(f'create schema if not exists "{DATABASE_SCHEMA}"')
    return _pool


def make_saver():
    """一段对话的存档。"""
    if not DATABASE_URL:
        return InMemorySaver()
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(_connections())
    saver.setup()
    return saver


def make_store():
    """跨多次出行的长期偏好。"""
    if not DATABASE_URL:
        return InMemoryStore()
    from langgraph.store.postgres import PostgresStore

    store = PostgresStore(_connections())
    store.setup()
    return store


def save_guide(title: str, destination: str, summary: str, content: str) -> int | None:
    """存一份终稿，返回新行的编号。没配数据库就不存。"""
    if not DATABASE_URL or not content.strip():
        return None
    with _connections().connection() as conn:
        row = conn.execute(
            "insert into public.travel_guide (title, destination, summary, content) "
            "values (%s, %s, %s, %s) returning id",
            (title, destination, summary, content),
        ).fetchone()
    return row["id"]
