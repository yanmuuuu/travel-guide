import json
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from app import auth
from app.db import get_guide, list_guides, make_saver, save_guide
from app.deep.agent import build, card_text
from app.guide.run import graph as guide_graph
from app.guide.store import UserPreference, load_preference, memory_store, save_preference

"""把两张图包成 HTTP 服务。

招聘里这叫服务化：FastAPI 提供接口，深度思考用 SSE 把过程流出来。
对话编号分开存。两张图的状态不能写进同一个存档。
点了深度思考，按同一个对话编号读出固定行程图的卡片，作为深度思考的第一句。
"""

app = FastAPI(title="travel-guide")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
# 两张图可能存进同一套表，深度思考的对话编号加前缀，不然会读坏对方的状态。
deep_graph = build(checkpointer=make_saver())


def _deep_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": f"deep:{thread_id}"}}


class GuideIn(BaseModel):
    """快速模式的一句需求。"""

    text: str
    thread_id: str = "thread-1"


class AuthIn(BaseModel):
    username: str
    password: str


def _thread(user: dict, thread_id: str) -> str:
    """对话编号按人隔开，猜到别人的编号也读不到。"""
    return f"{user['uid']}:{thread_id}"


class DeepIn(BaseModel):
    """深度思考的一句需求，或者对上一个问题的回答。"""

    thread_id: str = "thread-1"
    message: str = ""
    resume: str = Field(default="", description="上一次停下来问人之后，用户的选择")


@app.get("/health")
def health():
    """进程还在就返回 ok。不调模型。"""
    return {"ok": True}


def _session(user: dict) -> dict:
    token = auth.make_token(user)
    return {"token": token, "user": {"id": f"user-{user['id']}", "username": user["username"]}}


@app.post("/auth/register")
def register(body: AuthIn):
    return _session(auth.register(body.username, body.password))


@app.post("/auth/login")
def login(body: AuthIn):
    return _session(auth.login(body.username, body.password))


@app.get("/auth/me")
def me(user: dict = Depends(auth.current_user)):
    return {"id": user["uid"], "username": user["name"]}


@app.get("/preferences")
def preferences(user: dict = Depends(auth.current_user)):
    """系统记下的长期偏好，和快速模式读写的是同一份。"""
    saved = load_preference(memory_store, user["uid"])
    return UserPreference(**{**UserPreference().model_dump(), **saved}).model_dump()


@app.put("/preferences")
def update_preferences(body: UserPreference, user: dict = Depends(auth.current_user)):
    """用户手动改偏好，整份覆盖。"""
    data = {k: v.strip() for k, v in body.model_dump().items()}
    save_preference(memory_store, user["uid"], data)
    return data


@app.get("/guides")
def guides(limit: int = 50, user: dict = Depends(auth.current_user)):
    """当前用户深度思考存下的终稿列表。"""
    return list_guides(user["uid"], limit)


@app.get("/guides/{guide_id}")
def guide_detail(guide_id: int, user: dict = Depends(auth.current_user)):
    """一份终稿的正文。"""
    row = get_guide(user["uid"], guide_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这份行程")
    return row


@app.post("/guide")
def guide(body: GuideIn, user: dict = Depends(auth.current_user)):
    """跑固定行程图，一次返回终稿。这一张图不流式。"""
    result = guide_graph.invoke(
        {"messages": [HumanMessage(content=body.text)]},
        config={"configurable": {"thread_id": _thread(user, body.thread_id)}},
        context={"user_id": user["uid"]},
    )
    return {
        "destination": result.get("destination") or "",
        "origin": result.get("origin") or "",
        "days": result.get("days") or 0,
        "weather": result.get("weather") or "",
        "guide": result.get("guide") or "",
    }


@app.post("/deep/stream")
def deep_stream(body: DeepIn, user: dict = Depends(auth.current_user)):
    """跑深度思考，用 SSE 推出每一步。停下来问人时发 interrupt，然后这条流结束。

    用户选完再调一次，resume 里放选项原文。
    """
    thread_id = _thread(user, body.thread_id)
    config = _deep_config(thread_id)
    if body.resume:
        payload = Command(resume=body.resume)
    else:
        payload = {"messages": [HumanMessage(content=_first_message(thread_id, body.message))]}

    def events():
        interrupted = False
        trace = _Trace()
        for namespace, chunk in deep_graph.stream(payload, config, stream_mode="updates", subgraphs=True):
            pending = chunk.get("__interrupt__")
            if pending:
                interrupted = True
                value = pending[0].value if pending else ""
                yield _event({"type": "interrupt", "value": value})
                break
            for data in trace.feed(namespace, chunk):
                yield _event(data)
        if not interrupted:
            state = deep_graph.get_state(config)
            text = _last_text(state.values)
            guide_id = _keep(user["uid"], thread_id, text)
            yield _event({"type": "done", "text": text, "guide_id": guide_id})

    return StreamingResponse(events(), media_type="text/event-stream")


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _clip(text: str, limit: int = 800) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + f"…（共 {len(text)} 字）"


class _Trace:
    """把图的每一步更新翻成前端能看的思考过程：模型的想法、调了什么工具、工具回了什么。

    子代理跑在子图里，命名空间只有内部编号。主代理派 task 时记下描述，
    子图第一条人话和哪条描述对上，这个命名空间就是哪个子代理。
    """

    def __init__(self):
        self.agents: dict[tuple, str] = {(): "主代理"}
        self.tasks: dict[str, str] = {}  # task 描述 -> 子代理名
        self.calls: dict[str, str] = {}  # tool_call_id -> 工具名
        self.seen: set[str] = set()

    def _agent(self, namespace: tuple, messages: list) -> str:
        if namespace in self.agents:
            return self.agents[namespace]
        name = "子代理"
        for m in messages:
            if getattr(m, "type", "") == "human":
                name = self.tasks.pop(_text(m.content).strip(), name)
                break
        self.agents[namespace] = name
        return name

    def feed(self, namespace: tuple, chunk: dict):
        for node, update in chunk.items():
            if node.startswith("__") or not isinstance(update, dict):
                continue
            messages = update.get("messages")
            messages = getattr(messages, "value", messages)
            if not isinstance(messages, list):
                continue
            agent = self._agent(tuple(namespace), messages)
            for m in messages:
                mid = getattr(m, "id", None)
                if mid:
                    if mid in self.seen:
                        continue
                    self.seen.add(mid)
                yield from self._message(agent, m)

    def _message(self, agent: str, m):
        kind = getattr(m, "type", "")
        if kind == "ai":
            reasoning = (getattr(m, "additional_kwargs", {}) or {}).get("reasoning_content")
            if reasoning:
                yield {"type": "thought", "agent": agent, "text": _clip(reasoning, 1500)}
            calls = getattr(m, "tool_calls", None) or []
            text = _text(m.content).strip()
            if calls and text:
                yield {"type": "thought", "agent": agent, "text": _clip(text, 1500)}
            for call in calls:
                args = call.get("args") or {}
                self.calls[call.get("id", "")] = call["name"]
                if call["name"] == "task":
                    self.tasks[str(args.get("description", "")).strip()] = args.get("subagent_type") or "子代理"
                yield {
                    "type": "tool_call", "agent": agent, "id": call.get("id"),
                    "name": call["name"], "args": args,
                }
        elif kind == "tool":
            yield {
                "type": "tool_result", "agent": agent, "id": getattr(m, "tool_call_id", None),
                "name": getattr(m, "name", None) or self.calls.get(getattr(m, "tool_call_id", ""), ""),
                "content": _clip(_text(m.content)),
                "error": getattr(m, "status", "") == "error",
            }


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


def _keep(user_id: str, thread_id: str, text: str) -> int | None:
    """深度思考每写完一稿存一行，追问改过的也算新的一行。"""
    card = guide_graph.get_state({"configurable": {"thread_id": thread_id}}).values
    destination = card.get("destination") or ""
    title = f"{destination}{card.get('days') or ''}天行程" if destination else "深度思考行程"
    summary = text.strip().splitlines()[0][:200] if text.strip() else ""
    return save_guide(user_id, title, destination, summary, text)


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
