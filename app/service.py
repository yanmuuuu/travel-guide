import json

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.db import make_saver, save_guide
from app.deep.agent import build, card_text
from app.guide.run import graph as guide_graph

"""把两张图包成 HTTP 服务。

招聘里这叫服务化：FastAPI 提供接口，深度思考用 SSE 把过程流出来。
对话编号分开存。两张图的状态不能写进同一个存档。
点了深度思考，按同一个对话编号读出固定行程图的卡片，作为深度思考的第一句。
"""

app = FastAPI(title="travel-guide")
# 两张图可能存进同一套表，深度思考的对话编号加前缀，不然会读坏对方的状态。
deep_graph = build(checkpointer=make_saver())


def _deep_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": f"deep:{thread_id}"}}


class GuideIn(BaseModel):
    """快速模式的一句需求。"""

    text: str
    thread_id: str = "thread-1"
    user_id: str = "user-1"


class DeepIn(BaseModel):
    """深度思考的一句需求，或者对上一个问题的回答。"""

    thread_id: str = "thread-1"
    message: str = ""
    resume: str = Field(default="", description="上一次停下来问人之后，用户的选择")


@app.get("/health")
def health():
    """进程还在就返回 ok。不调模型。"""
    return {"ok": True}


@app.post("/guide")
def guide(body: GuideIn):
    """跑固定行程图，一次返回终稿。这一张图不流式。"""
    result = guide_graph.invoke(
        {"messages": [HumanMessage(content=body.text)]},
        config={"configurable": {"thread_id": body.thread_id}},
        context={"user_id": body.user_id},
    )
    return {
        "destination": result.get("destination") or "",
        "origin": result.get("origin") or "",
        "days": result.get("days") or 0,
        "weather": result.get("weather") or "",
        "guide": result.get("guide") or "",
    }


@app.post("/deep/stream")
def deep_stream(body: DeepIn):
    """跑深度思考，用 SSE 推出每一步。停下来问人时发 interrupt，然后这条流结束。

    用户选完再调一次，resume 里放选项原文。
    """
    config = _deep_config(body.thread_id)
    if body.resume:
        payload = Command(resume=body.resume)
    else:
        payload = {"messages": [HumanMessage(content=_first_message(body.thread_id, body.message))]}

    def events():
        interrupted = False
        for chunk in deep_graph.stream(payload, config, stream_mode="updates"):
            pending = chunk.get("__interrupt__")
            if pending:
                interrupted = True
                value = pending[0].value if pending else ""
                yield _event({"type": "interrupt", "value": value})
                break
            for node in chunk:
                if node.startswith("__"):
                    continue
                yield _event({"type": "update", "node": node})
        if not interrupted:
            state = deep_graph.get_state(config)
            text = _last_text(state.values)
            guide_id = _keep(body.thread_id, text)
            yield _event({"type": "done", "text": text, "guide_id": guide_id})

    return StreamingResponse(events(), media_type="text/event-stream")


def _first_message(thread_id: str, message: str) -> str:
    """深度思考这段对话的第一句，先抄上固定行程图里同一个对话编号的卡片。

    已经聊过了，或者固定行程图里没有这段对话，就原样用用户这一句。
    """
    if deep_graph.get_state(_deep_config(thread_id)).values.get("messages"):
        return message
    card = guide_graph.get_state({"configurable": {"thread_id": thread_id}}).values
    if not card.get("destination"):
        return message
    text = card_text(card)
    if message.strip():
        text += f"\n用户这一句：{message.strip()}"
    return text


def _keep(thread_id: str, text: str) -> int | None:
    """深度思考每写完一稿存一行，追问改过的也算新的一行。"""
    card = guide_graph.get_state({"configurable": {"thread_id": thread_id}}).values
    destination = card.get("destination") or ""
    title = f"{destination}{card.get('days') or ''}天行程" if destination else "深度思考行程"
    summary = text.strip().splitlines()[0][:200] if text.strip() else ""
    return save_guide(title, destination, summary, text)


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


def _event(data: dict) -> str:
    """一条 SSE。中文不转义，模型返回的对象用字符串兜住。"""
    return "data: " + json.dumps(data, ensure_ascii=False, default=str) + "\n\n"
