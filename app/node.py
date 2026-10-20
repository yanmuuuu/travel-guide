from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.constants import END
from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from app.context import ContextSchema
from app.llm import model
from app.schema import guide_critique, plan_parse
from app.search import search_web
from app.state import GuideState
from app.store import fill_blanks, load_preference, remember, save_preference


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
        f"行程：{days}天{nights}夜\n"
        f"去程：{go}\n回程：{back}\n"
        f"预算：{budget}\n人数：{party}"
    )
    preference = _preference_text(state.get("preferences", {}))
    if preference:
        return text + "\n" + preference
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


def _only_if_said(value: str, text: str) -> str:
    """这个词出现在这一句里，才算这一轮新说的。

    「杭州市」和用户说的「杭州」算同一个。上一轮沿用、这句没再提的，返回空，后面就不会写进偏好。
    """
    value = (value or "").strip()
    if not value or not text:
        return ""
    if value in text:
        return value
    short = value
    for suffix in ("市", "省"):
        if short.endswith(suffix) and len(short) > len(suffix):
            short = short[: -len(suffix)]
    if short and short in text:
        return value
    return ""


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
判断这句话是不是要规划行程。结合之前的对话。不是就结束。是的话抽出出发地、目的地、几天几夜、去程回程、预算和人数。没重新说的字段沿用上一轮。只有这一句里出现的出发地和交通才写入偏好。
"""
def parse_node(state: GuideState, runtime: Runtime[ContextSchema], *, store: BaseStore):
    user_text = current_user_text(state)
    history = dialogue_text(state, user_text)
    if history:
        heard = f"之前的对话：\n{history}\n\n这一句：{user_text}"
    else:
        heard = user_text
    result = model.with_structured_output(plan_parse).invoke([
        SystemMessage(content="你是一个根据描述提取信息的提取专家。请判断是不是要规划行程，并抽出出发地、目的地、几天几夜、去程、回程、这一趟的最低和最高总预算、大人小孩老人人数，以及这一轮说出的口味、忌口、兴趣、节奏、预算档次、住宿偏好、通常同行和规划习惯。如果这一句是在修改上一份行程，用户没重新说的出发地、目的地、人数、交通、预算，沿用之前对话里已经确定的，不要改成空。之前的对话里也没有，出发地和交通就留空。口味、忌口、兴趣、节奏、预算档次、住宿偏好、通常同行、规划习惯，只有这一句亲口说了才填，不要从之前的对话抄。没说的预算和人数填 0，不能猜测或编造。"),
        HumanMessage(content=heard),
    ])
    remembered = _remember_user(state, user_text)
    if not result.is_plan:
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
        "destination": result.destination.strip(),
        "origin": result.origin.strip(),
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
            "origin": _only_if_said(output["origin"], user_text),
            "transport_go": _only_if_said(output["transport_go"], user_text),
            "transport_back": _only_if_said(output["transport_back"], user_text),
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


def _merge_sources(sources: list[dict], found: list[dict]) -> None:
    """按网址并进来源。已经有的页面不再加。"""
    seen = {item.get("url") for item in sources}
    for item in found:
        url = item.get("url")
        if url and url not in seen:
            sources.append(item)
            seen.add(url)


"""-- 采集 --
先按目的地和几天几夜搜路线。兴趣、口味、住宿偏好、预算档、带小孩或老人再各补一条短查询，按网址并到一起。没有目的地，或一条都没搜到，后面会去道歉。
"""
def collect_node(state: GuideState):
    destination = (state.get("destination") or "").strip()
    if not destination:
        return {"sources": [], "rounds": 0}
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
        _merge_sources(sources, search_web(query))
    return {
        "sources": sources,
        "rounds": 0,
    }


"""-- 道歉 --
网上没有可用资料时停在这里，告诉用户这次抓不到，不再写稿。
"""
def apologize_node(state: GuideState):
    destination = state.get("destination") or "这次行程"
    return {
        "final_text": f"没抓到{destination}的资料，这次先停在这里。",
    }


"""-- 写稿 --
照着搜到的摘录写行程。夜数用来写入住、住几晚和退房。有修改意见时按意见重写一版。
"""
def generate_node(state: GuideState):
    blocks = []
    for index, source in enumerate(state.get("sources", []), 1):
        blocks.append(f"{index}. {source.get('title', '')}\n{source.get('snippet', '')}\n{source.get('url', '')}")
    feedback = state.get("feedback") or ""
    extra = f"\n上一版需要修改：\n{feedback}" if feedback else ""
    reply = model.invoke([
        SystemMessage(content="根据给出的网页摘录写一份中文行程。行程长度按几天几夜写。夜数是酒店晚数：要写清哪一晚入住、住几晚、哪一天退房。去程从出发地到目的地，回程从目的地回到出发地；出发地没说就不要编造从哪个城市出发。去程和回程按用户给出的方式安排在第一天和最后一天，没给出的方式不要编造班次。用户说了预算就按那个总预算。用户没说预算就按平价，每人每天450到500元，含吃住和市内交通；人数已知时总花费用人数乘天数再乘这个区间，人数未知就只按这个单价选平价，不要假设人数。不要编造摘录里没有的价格、班次和开放时间。摘录不够的地方标明待核实。"),
        HumanMessage(content=f"用户原话：{state.get('user_text', '')}\n{trip_text(state)}\n摘录：\n" + "\n\n".join(blocks) + extra),
    ])
    return {
        "guide": reply.content,
    }


"""-- 自检 --
看这版攻略能不能定稿。缺资料就去补搜，写得不好就去重写。已经转过两圈就直接通过。
"""
def critique_node(state: GuideState):
    # rounds 到 3 就不再挑刺，直接当通过，避免一直重写。
    if state.get("rounds", 0) >= 3:
        return {"critique": {"ok": True, "action": "none", "issues": [], "search_queries": []}}
    result = model.with_structured_output(guide_critique).invoke([
        SystemMessage(content="你在检查一份行程攻略。只根据用户要求和已有摘录判断。酒店晚数必须等于夜数。缺关键资料才要求补搜，小问题就要求重写，能用就通过。"),
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
        _merge_sources(sources, search_web(query))
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
是规划就去快答，否则结束。
"""
def route_after_parse(state: GuideState):
    if state.get("route") == "plan":
        return "quick_take"
    return END


"""-- 采集之后往哪走 --
有网页就去写稿，没有就去道歉。
"""
def route_after_collect(state: GuideState):
    if state.get("sources"):
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
