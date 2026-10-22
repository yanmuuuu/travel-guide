from app.deep.agent import build

"""深度思考图。给 Studio 用。

这里不能带 checkpointer。langgraph dev 自己会接一套记忆，图里再塞一套就会拒绝加载。
本地运行看 app/deep/run.py，那里才把内存记忆接上。ask 停下来问人需要那套记忆。
"""

graph = build()
