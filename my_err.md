# 我出现过的错误

## 1. `graph.draw_mermaid()` 用不了

报错：编译后的 `graph` 没有 `draw_mermaid` 这个方法。

原因：`builder.compile()` 得到的是用来运行的图。画图的方法在结构图上，要先 `get_graph()`。

改法：

```python
print(graph.get_graph().draw_mermaid())
```

这行打出来的是 Mermaid 文字。图片用 `graph.get_graph().draw_mermaid_png()` 写到文件里。

## 2. `invoke()` 缺少 `input`

报错：

```text
TypeError: Pregel.invoke() missing 1 required positional argument: 'input'
```

原因：第一个参数写成了 `state={...}`。`invoke` 要的名字是 `input`，不是 `state`，所以这份字典没传进去。

改法：字典直接放在第一个位置，上下文仍用 `context`。

```python
result = graph.invoke(
    {
        "user_text": text
    },
    context={
        "user_id": "user-1"
    }
)
```

本地要用 `InMemorySaver` 记住对话时，再加 `config={"configurable": {"thread_id": "thread-1"}}`。

## 3. `langgraph dev` 启动失败

报错：

```text
Application startup failed. Exiting.
```

原因：`graph.py` 里 `compile` 时带上了 `InMemorySaver` 和 `InMemoryStore`。`langgraph dev` 自己会接一套内存保存，发现图里又有一套，就拒绝加载。

改法：

- `graph.py` 的 `graph = builder.compile()` 不带 checkpointer 和 store，给 Studio 用。
- `run.py` 里另外 `builder.compile(checkpointer=memory_saver, store=memory_store)`，给 `python -m app.run` 用。
