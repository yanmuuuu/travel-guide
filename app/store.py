from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field


"""测试用，数据只活在这次进程里，关掉就没了。以后换成数据库时，只替换这两个对象。"""
memory_store = InMemoryStore()   # 长期偏好：口味、忌口、常驻城市，跨多次出行
memory_saver = InMemorySaver()   # 这一段对话：上一轮说了什么


"""用户偏好。只放下次出行仍然成立的内容，不记这一趟的天数、人数和总价。"""
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
    """这个人的偏好放在哪一格。第一格是用户编号，第二格固定叫 preference，避免和对话记录混在一起。"""
    return (user_id, "preference")


def load_preference(store: BaseStore, user_id: str) -> dict:
    """读这个人上次记下的偏好。库里还没有这个人，就给一张全空的表，后面按「没填」来处理。"""
    item = store.get(_namespace(user_id), "profile")
    if item is None:
        return UserPreference().model_dump()
    return item.value


def save_preference(store: BaseStore, user_id: str, data: dict) -> None:
    """整份偏好写回去。同一格只留一份，新的盖掉旧的。"""
    store.put(_namespace(user_id), "profile", data)


def fill_blanks(current: dict, saved: dict) -> dict:
    """补这一趟没说的空。这一句已经写了的字段不动。

    出发地空着，用常驻城市。去程、回程空着，用这个人常坐的方式。
    口味、忌口这些不在这里补，写稿时另附在行程说明后面。
    """
    filled = dict(current)
    if not filled.get("origin") and saved.get("home_city"):
        filled["origin"] = saved["home_city"]
    if not filled.get("transport_go") and saved.get("transport_go"):
        filled["transport_go"] = saved["transport_go"]
    if not filled.get("transport_back") and saved.get("transport_back"):
        filled["transport_back"] = saved["transport_back"]
    return filled


def remember(saved: dict, stated: dict) -> dict:
    """把这一轮亲口说了的写进长期偏好。没提到的保持上次的，不拿空字符串去擦掉旧记录。

    出发地记成常驻城市，下次没说从哪走时还能补上。天数、人数、这一趟的总价不进这里。
    """
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
