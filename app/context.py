from typing import TypedDict


class ContextSchema(TypedDict):
    user_id: str            # 这次是谁在跑，图运行过程中不改
