from datetime import date, timedelta

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.deep.agent import build, card_text

"""用同一段对话编号接着跑深度思考。

固定行程图和这张图的字段不一样，不能共用一个存档，否则会读坏。
做法是把那张卡片抄进第一条消息，对话编号仍用 thread-1，方便两边对照。
"""

memory = InMemorySaver()
graph = build(checkpointer=memory)


def _last_text(result: dict) -> str:
    """取出代理最后一段正文。"""
    messages = result.get("messages") or []
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


if __name__ == "__main__":
    # 12306 只卖今天起 14 天。示例放在第 7 天，避免一跑就因没开售被收成没查到。
    start = date.today() + timedelta(days=7)
    card = card_text({
        "destination": "杭州",
        "origin": "武汉",
        "start_date": start.isoformat(),
        "days": 3,
        "nights": 2,
        "transport_go": "高铁",
        "transport_back": "高铁",
        "budget_min": 0,
        "budget_max": 3000,
        "adults": 2,
        "children": 0,
        "seniors": 0,
        "notes": "",
        "guide": "西湖、灵隐一带，班次和房价待核实。",
    })
    config = {"configurable": {"thread_id": "thread-1"}}
    result = graph.invoke({"messages": [HumanMessage(content=card)]}, config)
    # 停下来问人时，结果在 interrupts 里，不在最后一条正文里。
    pending = getattr(result, "interrupts", None) or result.get("__interrupt__")
    if pending:
        print("要用户选：", pending)
        print("用户答完后，用同一个 config 恢复：")
        print('graph.invoke(Command(resume="选第一趟"), config)')
    else:
        print(_last_text(result))
