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
    route_after_note_judge,
    route_after_note_search,
    route_after_parse,
    note_judge_node,
    note_keep_node,
    note_rewrite_node,
    note_search_node,
)
from app.context import ContextSchema
from app.state import GuideState


"""4.定义图"""
builder = StateGraph(GuideState, context_schema=ContextSchema)

"""5.添加节点"""
builder.add_node("parse", parse_node)            # 听懂需求
builder.add_node("quick_take", quick_take_node)  # 先给一句快答
builder.add_node("note_search", note_search_node)  # 补充条件，只搜一次
builder.add_node("note_judge", note_judge_node)    # 新网页能不能补上
builder.add_node("note_rewrite", note_rewrite_node)  # 能补上就重写
builder.add_node("note_keep", note_keep_node)      # 不能补上就留原计划
builder.add_node("collect", collect_node)        # 每条都搜网页和小红书，有日期再查天气
builder.add_node("apologize", apologize_node)    # 网页和天气都没有就停下
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
    ["quick_take", "note_search", END],
)
builder.add_edge("quick_take", "collect")
builder.add_conditional_edges(
    "note_search",
    route_after_note_search,
    ["note_judge", "note_keep"],
)
builder.add_conditional_edges(
    "note_judge",
    route_after_note_judge,
    ["note_rewrite", "note_keep"],
)
builder.add_edge("note_rewrite", "critique")
builder.add_edge("note_keep", END)
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
