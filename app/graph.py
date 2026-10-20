from langgraph.constants import START, END
from langgraph.graph import StateGraph

from app.node import (
    apologize_node,
    collect_node,
    critique_node,
    finalize_node,
    generate_node,
    parse_node,
    quick_take_node,
    research_node,
    rewrite_node,
    route_after_collect,
    route_after_critique,
    route_after_parse,
)
from app.context import ContextSchema
from app.state import GuideState


"""4.定义图"""
builder = StateGraph(GuideState, context_schema=ContextSchema)

"""5.添加节点"""
builder.add_node("parse", parse_node)            # 听懂需求
builder.add_node("quick_take", quick_take_node)  # 先给一句快答
builder.add_node("collect", collect_node)        # 搜网页
builder.add_node("apologize", apologize_node)    # 没搜到就停下
builder.add_node("generate", generate_node)      # 按摘录写行程
builder.add_node("critique", critique_node)      # 检查能不能定稿
builder.add_node("research", research_node)      # 缺资料再搜
builder.add_node("rewrite", rewrite_node)        # 记下要改的点
builder.add_node("finalize", finalize_node)      # 交出终稿

"""6.添加边"""
builder.add_edge(START, "parse")
builder.add_conditional_edges(
    "parse",
    route_after_parse,
    ["quick_take", END],
)
builder.add_edge("quick_take", "collect")
builder.add_conditional_edges(
    "collect",
    route_after_collect,
    ["generate", "apologize"],
)
builder.add_edge("apologize", END)
builder.add_edge("generate", "critique")
builder.add_conditional_edges(
    "critique",
    route_after_critique,
    ["finalize", "research", "rewrite"],
)
builder.add_edge("research", "generate")
builder.add_edge("rewrite", "generate")
builder.add_edge("finalize", END)

"""7.编译"""
# Studio 的 langgraph dev 会自己接内存里的保存，这里不能再塞 checkpointer 和 store。
graph = builder.compile()

# print(graph.get_graph().draw_mermaid())
