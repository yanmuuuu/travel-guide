from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field


"""测试用。以后换成数据库时，只替换这两个对象。"""
memory_store = InMemoryStore()
memory_saver = InMemorySaver()


"""用户偏好。只放下次出行仍然成立的内容。"""
class UserPreference(BaseModel):
    taste: str = Field(default="", description="口味偏好")
    allergy: str = Field(default="", description="忌口过敏")
    interest: str = Field(default="", description="兴趣偏好")
    pace: str = Field(default="", description="节奏偏好")
    budget_habit: str = Field(default="", description="预算档次，例如平价。不是这一趟的总价")
    lodging: str = Field(default="", description="住宿偏好")
    transport_go: str = Field(default="", description="常坐的去程方式")
    transport_back: str = Field(default="", description="常坐的回程方式")
    home_city: str = Field(default="", description="常驻城市，用来补出发地")
    companions: str = Field(default="", description="通常和谁走，不记这一次的人数")
    planning_habit: str = Field(default="", description="规划习惯")


def _namespace(user_id: str) -> tuple[str, str]:
    return (user_id, "preference")


def load_preference(store: BaseStore, user_id: str) -> dict:
    """读这个人的偏好。还没有就给一张空表。"""
    item = store.get(_namespace(user_id), "profile")
    if item is None:
        return UserPreference().model_dump()
    return item.value


def save_preference(store: BaseStore, user_id: str, data: dict) -> None:
    """整份偏好写回去。"""
    store.put(_namespace(user_id), "profile", data)


def fill_blanks(current: dict, saved: dict) -> dict:
    """当前对话里有的字段以当前为主。空着的才用偏好补。"""
    filled = dict(current)
    if not filled.get("origin") and saved.get("home_city"):
        filled["origin"] = saved["home_city"]
    if not filled.get("transport_go") and saved.get("transport_go"):
        filled["transport_go"] = saved["transport_go"]
    if not filled.get("transport_back") and saved.get("transport_back"):
        filled["transport_back"] = saved["transport_back"]
    return filled


def remember(saved: dict, stated: dict) -> dict:
    """这一轮说了的才改偏好。没说的偏好保持原样。"""
    updated = dict(saved)
    if stated.get("origin"):
        updated["home_city"] = stated["origin"]
    for key in (
        "taste", "allergy", "interest", "pace", "budget_habit", "lodging",
        "transport_go", "transport_back", "companions", "planning_habit",
    ):
        if stated.get(key):
            updated[key] = stated[key]
    return updated
