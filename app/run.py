from langchain_core.messages import HumanMessage

from app.graph import builder
from app.store import memory_saver, memory_store

# 本地运行用内存保存对话和偏好。Studio 不走这个，它自己有一套。
graph = builder.compile(checkpointer=memory_saver, store=memory_store)


# 只在直接运行这个文件时才往下走。别的文件 import 它时不会自动跑图。
if __name__ == "__main__":
    text = "帮我安排杭州三日游，要住酒店, 两个大学生, 从武汉出发"
    print("用户:", text)

    result = graph.invoke(
        {
            "messages": [HumanMessage(content=text)]
        },
        config={
            "configurable": {
                "thread_id": "thread-1"
            }
        },
        context={
            "user_id": "user-1"
        }
    )

    print("是不是规划:", result.get("route"))
    print("目的地:", result.get("destination"))
    print("出发地:", result.get("origin"))
    print("天数:", result.get("days"))
    print("夜数:", result.get("nights"))
    print("去程:", result.get("transport_go"))
    print("回程:", result.get("transport_back"))
    print("最低预算:", result.get("budget_min"))
    print("最高预算:", result.get("budget_max"))
    print("大人:", result.get("adults"))
    print("小孩:", result.get("children"))
    print("老人:", result.get("seniors"))
    print("快答:", result.get("quick_take"))
    print("结果:", result.get("final_text"))
