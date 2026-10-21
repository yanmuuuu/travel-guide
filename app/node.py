from datetime import date
import asyncio
import threading

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.constants import END
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from app.context import ContextSchema
from app.llm import model
from app.schema import guide_critique, note_fit, plan_parse
from app.mcp.search import search_web
from app.mcp.xhs import search_notes
from app.mcp.weather import forecast
from app.state import GuideState
from app.store import fill_blanks, load_preference, remember, save_preference


def _run(coro):
    """同步调用也能跑搜索和天气。外面已经有事件循环时，改到新线程里跑。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}

    def runner():
        try:
            box["value"] = asyncio.run(coro)
        except Exception as exc:
            box["error"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _count(value: int) -> int:
    """没说或填了负数，都当成 0。人数和预算不能是负的。"""
    return value if value > 0 else 0


# 用户没说预算时按平价：每人每天 450 到 500 元
PLAIN_BUDGET_MIN = 450
PLAIN_BUDGET_MAX = 500


def _people_count(state: GuideState) -> int:
    """大人、小孩、老人加在一起。某一类没说就是 0，不计入。"""
    return state.get("adults", 0) + state.get("children", 0) + state.get("seniors", 0)


def _budget_text(state: GuideState) -> str:
    """把预算写成一句给模型看的话。

    用户说了最低或最高，就用这一趟的总价。
    都没说就按平价，每人每天 450 到 500 元。人数知道才乘出合计；不知道就只说单价，不当成 1 个人。
    """
    low = state.get("budget_min", 0)
    high = state.get("budget_max", 0)
    if low and high:
        return f"{low}到{high}元（这一趟总预算）"
    if high:
        return f"不超过{high}元（这一趟总预算）"
    if low:
        return f"不低于{low}元（这一趟总预算）"
    people = _people_count(state)
    days = state.get("days", 1)
    if people > 0:
        total_low = PLAIN_BUDGET_MIN * people * days
        total_high = PLAIN_BUDGET_MAX * people * days
        return (
            f"用户没说预算，按平价：每人每天{PLAIN_BUDGET_MIN}到{PLAIN_BUDGET_MAX}元，"
            f"{people}人{days}天合计约{total_low}到{total_high}元"
        )
    return f"用户没说预算，按平价：每人每天{PLAIN_BUDGET_MIN}到{PLAIN_BUDGET_MAX}元。人数未说明，不要乘出总价，也不要假设成1个人"


def trip_text(state: GuideState) -> str:
    """把这一趟已经确定的条件收成一段，快答、搜索和写稿都读它。

    没说的出发地、交通、人数写成「未说明」，不替用户编。有长期偏好就附在最后。
    """
    days = state.get("days", 1)
    nights = state.get("nights", 0)
    go = state.get("transport_go") or "未说明"
    back = state.get("transport_back") or "未说明"
    budget = _budget_text(state)
    people = []
    if state.get("adults"):
        people.append(f"大人{state.get('adults')}人")
    if state.get("children"):
        people.append(f"小孩{state.get('children')}人")
    if state.get("seniors"):
        people.append(f"老人{state.get('seniors')}人")
    party = "、".join(people) if people else "未说明"
    text = (
        f"目的地：{state.get('destination') or '未说明'}\n"
        f"出发地：{state.get('origin') or '未说明'}\n"
        f"出发日期：{state.get('start_date') or '未说明'}\n"
        f"行程：{days}天{nights}夜\n"
        f"去程：{go}\n回程：{back}\n"
        f"预算：{budget}\n人数：{party}"
    )
    weather = (state.get("weather") or "").strip()
    if weather:
        text += "\n" + weather
    preference = _preference_text(state.get("preferences", {}))
    if preference:
        text += "\n" + preference
    notes = (state.get("notes") or "").strip()
    if notes:
        text += "\n这一趟的补充条件（安排时要遵守）：\n" + notes
    return text


def _preference_text(saved: dict) -> str:
    """长期偏好里填过的项收成几行，空的跳过。

    写稿时只用来补这一趟没写的内容，不能改掉上面已经确定的目的地、天数和预算。
    """
    labels = (
        ("taste", "口味"),
        ("allergy", "忌口过敏"),
        ("interest", "兴趣"),
        ("pace", "节奏"),
        ("budget_habit", "预算档次"),
        ("lodging", "住宿偏好"),
        ("transport_go", "常去程"),
        ("transport_back", "常回程"),
        ("home_city", "常驻城市"),
        ("companions", "通常同行"),
        ("planning_habit", "规划习惯"),
    )
    lines = [f"{label}：{saved.get(key)}" for key, label in labels if saved.get(key)]
    if not lines:
        return ""
    return "长期偏好（只补充上面没写的内容，不要覆盖已经确定的字段）：\n" + "\n".join(lines)


def _message_text(message) -> str:
    """取出一条消息里的文字。内容有时是一整段，有时拆成好几块，这里拼成一句。"""
    content = message.content if hasattr(message, "content") else message.get("content", "")
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict):
                texts.append(block.get("text", ""))
        return "".join(texts)
    return content or ""


def _message_kind(message) -> str:
    """看这条是用户还是助手。消息可能是对象，也可能是字典，两种都认。"""
    kind = getattr(message, "type", "")
    if kind:
        return kind
    if isinstance(message, dict):
        return message.get("type") or message.get("role") or ""
    return ""


def current_user_text(state: GuideState) -> str:
    """这一轮用户说的话。

    从对话末尾往前找最后一条用户消息。对话里还没有用户消息时，才用单独存的 user_text。
    """
    for message in reversed(state.get("messages") or []):
        if _message_kind(message) in ("human", "user"):
            text = _message_text(message).strip()
            if text:
                return text
    return (state.get("user_text") or "").strip()


def _start_date(year: int, month: int, day: int) -> str:
    """把模型收好的年月日收成出发日期。没有四位年份就只留月日。"""
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return ""
    if year > 0:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    return f"{month}月{day}日"


def dialogue_text(state: GuideState, current: str = "") -> str:
    """把之前的对话拼成「用户：…」「助手：…」。

    传了 current 时，最后一条如果就是这一句，先拿掉，避免同一句话既当历史又当当前。
    """
    messages = list(state.get("messages") or [])
    if (
        current
        and messages
        and _message_kind(messages[-1]) in ("human", "user")
        and _message_text(messages[-1]).strip() == current
    ):
        messages = messages[:-1]
    lines = []
    for message in messages:
        kind = _message_kind(message)
        who = "用户" if kind in ("human", "user") else "助手"
        lines.append(f"{who}：{_message_text(message)}")
    return "\n".join(lines)


def _remember_user(state: GuideState, text: str) -> list:
    """这句还没在对话里时，补一条用户消息。最后一条已经是这句，就不再加。"""
    messages = state.get("messages", [])
    if messages and _message_text(messages[-1]).strip() == text:
        return []
    return [HumanMessage(content=text)]


"""-- 听懂需求 --
判断这句话是规划、给已有行程加条件，还是无关。加条件就记下来并只搜一次。改字段以这一句为准。只有这一句里新表达的出发地和交通才写入偏好。
"""
def parse_node(state: GuideState, runtime: Runtime[ContextSchema], *, store: BaseStore):
    user_text = current_user_text(state)
    history = dialogue_text(state, user_text)
    card = trip_text(state)
    guide = (state.get("guide") or "").strip()
    if guide:
        card += "\n已有行程：\n" + guide[:1500]
    if history:
        heard = f"已有条件：\n{card}\n\n之前的对话：\n{history}\n\n这一句：{user_text}"
    else:
        heard = f"已有条件：\n{card}\n\n这一句：{user_text}"
    today = date.today().isoformat()
    result = model.with_structured_output(plan_parse).invoke([
        SystemMessage(content=f"你是一个根据描述提取信息的提取专家。今天是{today}。请先看之前的对话和已有行程，再看这一句。人可以用很多说法表达同一个意思，请收成表里的固定字段。intent 三选一：要求规划或修改日期、目的地、天数，填 plan；给已有行程加条件，例如可以接受打伞出去玩，填 note；闲聊、问好、与这次行程无关，填 end。是 plan 时抽出出发地、目的地、出发年、月、日、几天几夜、去程、回程、这一趟的最低和最高总预算、大人小孩老人人数，以及这一轮说出的口味、忌口、兴趣、节奏、预算档次、住宿偏好、通常同行和规划习惯。这一句提到的字段以这一句为准。只说日子、没说月份时，月份用对话里已经确定的月份，例如已经是 10 月 22 日、这一句说 29 号，start_month 填 10，start_day 填 29，不要留着 22。没说出四位年份时 start_year 填 0，不要补年份，也不要推算今年还是明年。只说日子、对话里还没有月份时，用今天的月份。这一句没提到的出发地、目的地、日期、人数、交通、预算，沿用之前对话里已经确定的，不要改成空。之前的对话里也没有，出发地、交通留空，日期填 0。stated_origin、stated_transport_go、stated_transport_back 只填这一句新表达的，沿用上一轮的不要填。口味、忌口、兴趣、节奏、预算档次、住宿偏好、通常同行、规划习惯，也只有这一句亲口说了才填。没说的预算和人数填 0，不能猜测或编造。intent 为 note 时，note 写成一句约束。note_query 只写目的地加一件事，不要写日期，不要把用户原话整句放进去。可以打伞出门写成「目的地 雨天 户外」或「目的地 小雨 步行」。要找室内才写成「目的地 下雨天 室内」。"),
        HumanMessage(content=heard),
    ])
    remembered = _remember_user(state, user_text)
    if result.intent == "end":
        reply = "这句不是行程规划，图在这里结束。"
        output = {
            "user_text": user_text,
            "route": "end",
            "final_text": reply,
            "messages": [AIMessage(content=reply)],
        }
        if remembered:
            output["messages"] = remembered + output["messages"]
        return output
    if result.intent == "note":
        output = {
            "user_text": user_text,
            "route": "note",
            "notes": _append_note(state.get("notes", ""), result.note.strip()),
            "note_query": result.note_query.strip(),
        }
        if remembered:
            output["messages"] = remembered
        return output
    days = result.days if result.days > 0 else 1
    nights = result.nights if result.nights > 0 else 0
    hotel_needed = result.hotel_needed or nights > 0
    # 要住但没说几晚：多日行程按天数减 1。不住宿就把夜数清掉。
    if hotel_needed and nights == 0 and days > 1:
        nights = days - 1
    if not hotel_needed:
        nights = 0
    budget_min = _count(result.budget_min)
    budget_max = _count(result.budget_max)
    # 最低比最高还大，说明说反了，对调过来。
    if budget_min and budget_max and budget_min > budget_max:
        budget_min, budget_max = budget_max, budget_min
    output = {
        "user_text": user_text,
        "route": "plan",
        "note_query": "",
        "destination": result.destination.strip(),
        "origin": result.origin.strip(),
        "start_date": _start_date(result.start_year, result.start_month, result.start_day),
        "days": days,
        "nights": nights,
        "hotel_needed": hotel_needed,
        "transport_go": result.transport_go.strip(),
        "transport_back": result.transport_back.strip(),
        "budget_min": budget_min,
        "budget_max": budget_max,
        "adults": _count(result.adults),
        "children": _count(result.children),
        "seniors": _count(result.seniors),
    }
    # 这一轮说了的以这一轮为准。空着的字段才用 store 里的偏好补上，再说了的就改掉旧偏好。
    # 出发地和交通必须出现在这一句里才写进偏好。从上一轮沿用来的不写。
    user_id = runtime.context.get("user_id", "")
    if user_id:
        saved = load_preference(store, user_id)
        stated = {
            "origin": result.stated_origin.strip(),
            "transport_go": result.stated_transport_go.strip(),
            "transport_back": result.stated_transport_back.strip(),
            "taste": result.taste.strip(),
            "allergy": result.allergy.strip(),
            "interest": result.interest.strip(),
            "pace": result.pace.strip(),
            "budget_habit": result.budget_habit.strip(),
            "lodging": result.lodging.strip(),
            "companions": result.companions.strip(),
            "planning_habit": result.planning_habit.strip(),
        }
        output = fill_blanks(output, saved)
        saved = remember(saved, stated)
        save_preference(store, user_id, saved)
        output["preferences"] = saved
    if remembered:
        output["messages"] = remembered
    return output


def _append_note(saved: str, note: str) -> str:
    """把这一句约束接到这一趟后面。同一句不重复记。"""
    note = (note or "").strip()
    saved = (saved or "").strip()
    if not note or note in saved:
        return saved
    if not saved:
        return note
    return saved + "\n" + note


"""-- 补充搜索 --
给已有行程加了条件。有行程、也有搜索词，就只搜一次。没有行程或没有搜索词，后面保留原计划。
"""
def note_search_node(state: GuideState):
    guide = (state.get("guide") or "").strip()
    query = (state.get("note_query") or "").strip()
    if not guide:
        return {"note_hits": [], "note_step": "no_plan"}
    if not query:
        return {"note_hits": [], "note_step": "no_query"}
    found = _run(search_web(query)) or []
    _merge_sources(found, _run(search_notes(query)) or [])
    sources = list(state.get("sources") or [])
    _merge_sources(sources, found)
    if not found:
        return {"note_hits": [], "sources": sources, "note_step": "no_hit"}
    return {"note_hits": found, "sources": sources, "note_step": "judge"}


"""-- 补充之后往哪走 --
搜到新网页就去判断能不能补上。没有就保留原计划。
"""
def route_after_note_search(state: GuideState):
    if state.get("note_step") == "judge":
        return "note_judge"
    return "note_keep"


"""-- 判断能不能补上 --
新摘录能改某天去哪，或按这句话把雨天改成户外，才去重写。重复的景点留下原计划。
"""
def note_judge_node(state: GuideState):
    blocks = []
    for index, source in enumerate(state.get("note_hits") or [], 1):
        blocks.append(f"{index}. {source.get('title', '')}\n{source.get('snippet', '')}\n{source.get('url', '')}")
    result = model.with_structured_output(note_fit).invoke([
        SystemMessage(content="你在看一句新条件和刚搜到的网页，判断要不要改已有行程。新内容能改某天去哪，或让雨天按这句话改成户外，才算能补上。只是重复已有景点就不要改。"),
        HumanMessage(content=f"补充条件：{state.get('notes', '')}\n{trip_text(state)}\n已有行程：\n{state.get('guide', '')}\n新摘录：\n" + "\n\n".join(blocks)),
    ])
    if result.useful:
        return {"note_step": "rewrite"}
    return {"note_step": "repeat"}


"""-- 判断之后往哪走 --
能补上就重写。不能补上就保留原计划。
"""
def route_after_note_judge(state: GuideState):
    if state.get("note_step") == "rewrite":
        return "note_rewrite"
    return "note_keep"


"""-- 按补充重写 --
用已有行程和新摘录写一版，然后交给原来的自检。缺资料会补搜，写得不好会重写。
"""
def note_rewrite_node(state: GuideState):
    blocks = []
    for index, source in enumerate(state.get("note_hits") or [], 1):
        blocks.append(f"{index}. {source.get('title', '')}\n{source.get('snippet', '')}\n{source.get('url', '')}")
    reply = model.invoke([
        SystemMessage(content="按补充条件和摘录改写已有行程。能改的日子就改去处和顺序，其余天保持原计划。价格、班次和开放时间只使用摘录里有的，没有就标待核实。"),
        HumanMessage(content=f"补充条件：{state.get('notes', '')}\n{trip_text(state)}\n已有行程：\n{state.get('guide', '')}\n新摘录：\n" + "\n\n".join(blocks)),
    ])
    guide = reply.content.strip()
    return {
        "guide": guide,
        "final_text": guide,
        "messages": [AIMessage(content=guide)],
    }


"""-- 保留原计划 --
没有可补上的内容时停在这里，把已记下的条件和原行程交回去。
"""
def note_keep_node(state: GuideState):
    guide = (state.get("guide") or "").strip()
    step = state.get("note_step") or ""
    if step == "no_plan" or not guide:
        reply = "这句话先记下了。还没有行程，先不改计划。"
    elif step == "repeat":
        reply = f"搜到的内容和现有行程重复，计划先不改。这句话已经记下。\n\n{guide}"
    else:
        reply = f"按这句话搜了一轮，没有能补上的内容，行程先保持原样。\n\n{guide}"
    return {
        "final_text": reply,
        "messages": [AIMessage(content=reply)],
    }


"""-- 快答 --
采集还没开始，先给一句不超过 120 字的初步安排，让用户不用干等。
"""
def quick_take_node(state: GuideState):
    reply = model.invoke([
        SystemMessage(content="用不超过120字给出初步安排。不要编造价格、班次和开放时间。不要说你准备去搜索。"),
        HumanMessage(content=f"{trip_text(state)}\n用户原话：{state.get('user_text', '')}"),
    ])
    return {
        "quick_take": reply.content,
        "messages": [AIMessage(content=reply.content)],
    }


def _stated(saved: dict, key: str) -> str:
    """偏好里这一项有文字才拿来拼搜索词。"""
    value = saved.get(key) or ""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _extra_queries(state: GuideState, destination: str) -> list[str]:
    """粗搜之外，会换一批网页的条件各补一条。

    兴趣、口味、住宿偏好、预算档，以及这趟有小孩或老人。
    节奏、规划习惯、忌口只留给写稿，不单独搜。
    """
    saved = state.get("preferences") or {}
    queries = []
    interest = _stated(saved, "interest")
    if interest:
        queries.append(f"{destination} {interest}")
    taste = _stated(saved, "taste")
    if taste:
        queries.append(f"{destination} {taste} 餐厅")
    lodging = _stated(saved, "lodging")
    if lodging:
        queries.append(f"{destination} {lodging} 住宿")
    habit = _stated(saved, "budget_habit")
    if habit:
        queries.append(f"{destination} {habit} 吃住")
    if state.get("children"):
        queries.append(f"{destination} 亲子")
    if state.get("seniors"):
        queries.append(f"{destination} 适合老人")
    return queries


def _weather_queries(destination: str, weather: str) -> list[str]:
    """预报里有雨或雪，再搜这天能去的室内地方。晴天的景点已经在路线里搜过。"""
    if not weather:
        return []
    if any(word in weather for word in ("雨", "雪")):
        return [f"{destination} 下雨天 室内 景点"]
    return []


def _merge_sources(sources: list[dict], found: list[dict]) -> None:
    """按网址并进来源。已经有的页面不再加。"""
    seen = {item.get("url") for item in sources}
    for item in found:
        url = item.get("url")
        if url and url not in seen:
            sources.append(item)
            seen.add(url)


"""-- 采集 --
先按目的地和几天几夜搜路线。兴趣、口味、住宿偏好、预算档、带小孩或老人再各补一条短查询。说了出发日期就查天气，预报里有雨或雪再搜室内景点，并进同一批网页。网页和天气至少有一样，后面就去写稿。两样都没有才道歉。
"""
def collect_node(state: GuideState):
    destination = (state.get("destination") or "").strip()
    if not destination:
        return {"sources": [], "rounds": 0, "weather": ""}
    # 路线查询带上几天几夜。有住宿、出发地、交通就写进去。没说预算就加上「平价」。
    days = state.get("days", 1)
    nights = state.get("nights", 0)
    hotel = " 住宿" if nights else ""
    go = state.get("transport_go") or ""
    back = state.get("transport_back") or ""
    origin = state.get("origin") or ""
    transport = f" 从{origin}出发" if origin else ""
    transport += f" {go}去{back}回" if go or back else ""
    price = ""
    if not state.get("budget_min") and not state.get("budget_max"):
        price = " 平价"
    queries = [f"{destination}{days}天{nights}夜 路线{hotel}{transport}{price}"]
    queries.extend(_extra_queries(state, destination))
    sources = []
    for query in queries:
        _merge_sources(sources, _run(search_web(query)) or [])
    if queries:
        _merge_sources(sources, _run(search_notes(queries[0])) or [])
    start = (state.get("start_date") or "").strip()
    weather = _run(forecast(destination, start, days)) if start else ""
    for query in _weather_queries(destination, weather):
        _merge_sources(sources, _run(search_web(query)) or [])
        _merge_sources(sources, _run(search_notes(query)) or [])
    return {
        "sources": sources,
        "rounds": 0,
        "weather": weather,
    }


"""-- 道歉 --
网页和天气都没有时停在这里，告诉用户这次抓不到，不再写稿。
"""
def apologize_node(state: GuideState):
    destination = state.get("destination") or "这次行程"
    return {
        "final_text": f"没抓到{destination}的资料，这次先停在这里。",
    }


"""-- 写稿 --
有网页摘录就照着写。只有天气预报时也写，摘录里没有的价格、班次和开放时间标待核实。有修改意见时按意见重写一版。
"""
def generate_node(state: GuideState):
    blocks = []
    for index, source in enumerate(state.get("sources", []), 1):
        blocks.append(f"{index}. {source.get('title', '')}\n{source.get('snippet', '')}\n{source.get('url', '')}")
    feedback = state.get("feedback") or ""
    extra = f"\n上一版需要修改：\n{feedback}" if feedback else ""
    excerpts = "\n\n".join(blocks) if blocks else "没有网页摘录。"
    reply = model.invoke([
        SystemMessage(content="根据给出的材料写一份中文行程。有网页摘录就以摘录为准。没有网页摘录、但有天气预报或行程条件时，仍然要写出行程。用户这一句如果改了出发日期、目的地或天数，以这一句为准，不要用旧的已确认日期压过它。行程长度按几天几夜写。夜数是酒店晚数：要写清哪一晚入住、住几晚、哪一天退房。去程从出发地到目的地，回程从目的地回到出发地；出发地没说就不要编造从哪个城市出发。去程和回程按用户给出的方式安排在第一天和最后一天，没给出的方式不要编造班次。用户说了预算就按那个总预算。用户没说预算就按平价，每人每天450到500元，含吃住和市内交通；人数已知时总花费用人数乘天数再乘这个区间，人数未知就只按这个单价选平价，不要假设人数。不要编造材料里没有的价格、班次和开放时间，这些地方标明待核实。给出了天气预报时，只能使用这份预报写天气。没有天气预报就不要写天气。预报里有雨或雪的日子，按摘录写出具体的室内去处和顺序，不要只写适合室内。摘录里没有室内去处就标明待核实。"),
        HumanMessage(content=f"用户原话：{state.get('user_text', '')}\n{trip_text(state)}\n摘录：\n{excerpts}{extra}"),
    ])
    return {
        "guide": reply.content,
    }


"""-- 自检 --
看这版攻略能不能定稿。缺资料就去补搜，写得不好就去重写。已经转过三圈就直接通过。
"""
def critique_node(state: GuideState):
    # rounds 到 3 就不再挑刺，直接当通过，避免一直重写。
    if state.get("rounds", 0) >= 3:
        return {"critique": {"ok": True, "action": "none", "issues": [], "search_queries": []}}
    result = model.with_structured_output(guide_critique).invoke([
        SystemMessage(content="你在检查一份行程攻略。只根据用户要求和已有摘录判断。酒店晚数必须等于夜数。给出了天气预报时，攻略里的天气必须和预报一致。有雨或雪却没有具体去处，算缺资料。补搜用短查询，不要写日期，也不要写用户原话。用户接受雨天出门就写「目的地 雨天 户外」，否则写「目的地 下雨天 室内」。缺关键资料才要求补搜，小问题就要求重写，能用就通过。"),
        HumanMessage(content=f"用户原话：{state.get('user_text', '')}\n{trip_text(state)}\n攻略：\n{state.get('guide', '')}"),
    ])
    return {
        "critique": result.model_dump(),
    }


"""-- 补搜 --
自检指出还缺资料时，按它给的搜索词再搜一轮，把新页面并进已有来源。
"""
def research_node(state: GuideState):
    critique = state.get("critique", {})
    queries = critique.get("search_queries", [])
    sources = list(state.get("sources", []))
    # 网址已经有的不再加。这一轮没搜到新页面，就把问题交给下一轮写稿去改。
    before = len(sources)
    for query in queries:
        _merge_sources(sources, _run(search_web(query)) or [])
        _merge_sources(sources, _run(search_notes(query)) or [])
    feedback = ""
    if len(sources) == before:
        feedback = "\n".join(critique.get("issues", []))
    return {
        "rounds": state.get("rounds", 0) + 1,
        "sources": sources,
        "feedback": feedback,
    }


"""-- 重写 --
资料够用、只是写得不好时，把自检的问题记下来，交给下一轮写稿。这里不自己写正文。
"""
def rewrite_node(state: GuideState):
    critique = state.get("critique", {})
    issues = critique.get("issues", [])
    return {
        "rounds": state.get("rounds", 0) + 1,
        "feedback": "\n".join(issues),
    }


"""-- 定稿 --
自检通过后，把攻略正文交给用户。
"""
def finalize_node(state: GuideState):
    guide = state.get("guide", "")
    return {
        "final_text": guide,
        "messages": [AIMessage(content=guide)],
    }


"""-- 听懂之后往哪走 --
规划就去快答。给已有行程加条件就去补充。其余结束。
"""
def route_after_parse(state: GuideState):
    if state.get("route") == "plan":
        return "quick_take"
    if state.get("route") == "note":
        return "note_search"
    return END


"""-- 采集之后往哪走 --
有网页或天气预报就去写稿。两样都没有就去道歉。
"""
def route_after_collect(state: GuideState):
    if state.get("sources") or (state.get("weather") or "").strip():
        return "generate"
    return "apologize"


"""-- 自检之后往哪走 --
通过就定稿，缺资料就补搜，其余情况重写。
"""
def route_after_critique(state: GuideState):
    critique = state.get("critique", {})
    if critique.get("ok"):
        return "finalize"
    if critique.get("action") == "research" and critique.get("search_queries"):
        return "research"
    return "rewrite"
