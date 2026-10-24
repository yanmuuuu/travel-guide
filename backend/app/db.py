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
            # 远端库会掐掉闲置连接，借出前先探一下，坏的换新的。
            check=ConnectionPool.check_connection,
            max_idle=120,
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


_guide_owner_ready = False


def _guides():
    """终稿表按人分，老表第一次用时补上 user_id 列。"""
    global _guide_owner_ready
    pool = _connections()
    if not _guide_owner_ready:
        with pool.connection() as conn:
            conn.execute("alter table public.travel_guide add column if not exists user_id text")
            conn.execute(
                "create index if not exists travel_guide_user_id_idx on public.travel_guide (user_id, id desc)"
            )
        _guide_owner_ready = True
    return pool


def save_guide(user_id: str, title: str, destination: str, summary: str, content: str) -> int | None:
    """存一份终稿，返回新行的编号。没配数据库就不存。"""
    if not DATABASE_URL or not content.strip():
        return None
    with _guides().connection() as conn:
        row = conn.execute(
            "insert into public.travel_guide (user_id, title, destination, summary, content) "
            "values (%s, %s, %s, %s, %s) returning id",
            (user_id, title, destination, summary, content),
        ).fetchone()
    return row["id"]


def list_guides(user_id: str, limit: int = 50) -> list[dict]:
    """这个人最近存下的终稿，不带正文。没配数据库就是空的。"""
    if not DATABASE_URL:
        return []
    with _guides().connection() as conn:
        return conn.execute(
            "select id, title, destination, summary, created_at from public.travel_guide "
            "where user_id = %s order by id desc limit %s",
            (user_id, limit),
        ).fetchall()


def get_guide(user_id: str, guide_id: int) -> dict | None:
    """按编号取这个人的一份终稿，别人的当作不存在。"""
    if not DATABASE_URL:
        return None
    with _guides().connection() as conn:
        return conn.execute(
            "select id, title, destination, summary, content, created_at "
            "from public.travel_guide where id = %s and user_id = %s",
            (guide_id, user_id),
        ).fetchone()
