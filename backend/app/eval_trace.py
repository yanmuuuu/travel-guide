import json
import time
from datetime import date, timedelta
from pathlib import Path

"""给深度思考的轨迹打分。打分本身不调模型。

招聘里这叫 Agent 评测：不看文章写得顺不顺，看该派的子代理派了没有、该问人的问了没有、
终稿里有没有该有的东西。

    python -m app.eval_trace    跑下面几趟真实行程，结果写进 eval_results.json
"""

# 一趟完整规划至少要派这四个。少一个就算这次编排没命中。
NEED_AGENTS = ("route", "traffic", "stay", "food")
RESULT_FILE = Path(__file__).resolve().parents[1] / "eval_results.json"

# 出发日放在今天起 14 天内，12306 才查得到票。
CASES = [
    {"origin": "武汉", "destination": "杭州", "offset": 5, "days": 3, "nights": 2, "budget_max": 3000, "adults": 2},
    {"origin": "北京", "destination": "西安", "offset": 6, "days": 2, "nights": 1, "budget_max": 2500, "adults": 2},
    {"origin": "上海", "destination": "南京", "offset": 7, "days": 2, "nights": 1, "budget_max": 2000, "adults": 2, "seniors": 1},
    {"origin": "广州", "destination": "长沙", "offset": 8, "days": 3, "nights": 2, "budget_max": 2000, "adults": 1},
    {"origin": "成都", "destination": "重庆", "offset": 9, "days": 2, "nights": 1, "budget_max": 1500, "adults": 2, "notes": "爱吃辣"},
]


def calls_of(messages) -> list[dict]:
    """从主代理消息里取出工具调用。子代理内部的查询不在这层。"""
    found = []
    for message in messages or []:
        calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
        for call in calls or []:
            if not isinstance(call, dict):
                continue
            args = call.get("args") or {}
            found.append({
                "name": call.get("name") or "",
                "subagent": args.get("subagent_type") or "",
                "question": str(args.get("question") or ""),
            })
    return found


def score_trace(calls: list[dict], final: str = "") -> dict:
    """按轨迹和终稿逐项检查。全部通过才算 ok。

    - 四个子代理都派到
    - 去程、回程分开各问一次，酒店也问了
    - 终稿里有高德专属地图链接、有折后票价
    """
    agents = [call["subagent"] for call in calls if call["name"] == "task" and call["subagent"]]
    questions = [call["question"] for call in calls if call["name"] == "ask"]
    hit = [name for name in NEED_AGENTS if name in agents]
    checks = {
        "agents": len(hit) == len(NEED_AGENTS),
        "ask_go": any("去程" in text for text in questions),
        "ask_back": any(word in text for text in questions for word in ("回程", "返程")),
        "ask_hotel": any(word in text for text in questions for word in ("酒店", "住")),
    }
    if final:
        checks["map_link"] = "amapuri://" in final
        checks["fare_discount"] = "折" in final
    return {
        "ok": all(checks.values()),
        "agent_hit": f"{len(hit)}/{len(NEED_AGENTS)}",
        "missing": [name for name in NEED_AGENTS if name not in hit],
        "asks": len(questions),
        "checks": checks,
    }


def run_case(case: dict) -> dict:
    """真跑一趟深度思考。每次停下来问人，都选第一个选项。"""
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    from app.deep.agent import build, card_text

    graph = build(checkpointer=InMemorySaver())
    start = date.today() + timedelta(days=case["offset"])
    card = card_text({**case, "start_date": start.isoformat(), "transport_go": "高铁", "transport_back": "高铁"})
    config = {"configurable": {"thread_id": f"eval-{case['origin']}-{case['destination']}"}}
    began = time.time()
    result = graph.invoke({"messages": [HumanMessage(content=card)]}, config)
    for _ in range(12):
        pending = result.get("__interrupt__")
        if not pending:
            break
        value = pending[0].value
        options = value.get("options") if isinstance(value, dict) else None
        result = graph.invoke(Command(resume=options[0] if options else "按你推荐的来"), config)
    messages = graph.get_state(config).values.get("messages") or []
    final = str(messages[-1].content) if messages else ""
    score = score_trace(calls_of(messages), final)
    return {
        "case": f"{case['origin']}→{case['destination']} {case['days']}天{case['nights']}夜",
        "seconds": round(time.time() - began),
        **score,
        "final_head": final[:300],
    }


def run_all() -> None:
    """逐个跑完，算各项通过率，写进 eval_results.json。"""
    rows = []
    for case in CASES:
        try:
            row = run_case(case)
        except Exception as error:
            row = {"case": f"{case['origin']}→{case['destination']}", "ok": False, "error": str(error)[:300]}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    keys = sorted({key for row in rows for key in (row.get("checks") or {})})
    summary = {
        "cases": len(rows),
        "passed": sum(1 for row in rows if row.get("ok")),
        "check_rate": {
            key: f"{sum(1 for row in rows if (row.get('checks') or {}).get(key))}/{len(rows)}"
            for key in keys
        },
        "avg_seconds": round(sum(row.get("seconds", 0) for row in rows) / max(len(rows), 1)),
    }
    RESULT_FILE.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("汇总：", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    run_all()
