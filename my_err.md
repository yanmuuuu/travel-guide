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

## 4. 没说年份的出发日期会算错

现象：今天是 2026-10-21。说「12.5 去」应该是今年 12 月 5 日，说「1.5 去」应该是明年 1 月 5 日。实际会停在今年，或者根本读不出这个日期。

原因有两层：

- 听懂需求时，提示词写的是「没说年份就用今年」，模型把「1.5」填成 `2026-01-05`。
- `normalize_day` 一旦看见字符串里已经有年份，就原样收下，不再改成明年。`12.5`、`1.5`、`10.21` 这种点号写法也不认。

改法：年份交给模型填整数。没说出四位年份时 `start_year` 是 0，出发日期只显示「1月5日」。说了四位年份才显示 `YYYY-MM-DD`。查的时候再在今天起 40 天里对上这一天。

国内天气后来改成中国天气网：7 天内读 7 天页，第 8 到 15 天读 15 天页，再远读 40 天日历。海外城市走 Open-Meteo。月日对不上的那一天不写进预报。

## 5. 直接运行 `python app/graph.py` 报 FastMCP 没装

报错：

```text
ModuleNotFoundError: No module named 'mcp.client'
```

或者包成一句「FastMCP client support is not installed」。

原因：在 `app` 目录里运行时，自己的 `app/mcp` 盖住了安装好的 `mcp` 包。不在项目根运行，又会报 `No module named app`。`graph.py` 本身也不负责跑一趟行程。

改法：在项目根执行 `python -m app.run`。看 Studio 用 `langgraph dev`。

## 6. 问完只有快答，走到采集就停

报错：

```text
TypeError: No synchronous function provided to "collect"
```

现象看起来像搜完就结束，来源和天气都是空，然后道歉。

原因：`collect` 写成了 `async`。`invoke` 走的是同步节点，没有同步函数就在这里停下。搜索失败被收成空列表，空来源再加空天气就会道歉。

改法：`collect`、`research`、`note_search` 改成同步函数。里面要上网的部分用 `_run` 跑协程。外面已经有事件循环时，`_run` 改到新线程里跑。

## 7. 小红书服务看起来没反应

现象：日志停在 `启动 HTTP 服务器: :18060`。浏览器打开 `http://127.0.0.1:18060/mcp` 得到 400，还有一条 `favicon.ico` 的 404。开着梯子时，程序去连这个地址得到 502。

原因：

- 这个地址不是网页，只接受 MCP 的 POST。400 表示服务已经在听。
- 梯子会把 `127.0.0.1` 也送进代理，代理回 502。浏览器通常不代理本机，所以浏览器能打到服务，程序却不能。

改法：连这个本机地址时关闭系统代理（`trust_env=False`）。窗口要一直开着。登录程序只在第一次或登录过期时再跑，并且要和 `cookies.json` 放在同一个目录里启动。

## 8. 行程里只有网页，没有小红书

现象：小红书已经登录，攻略来源全是普通网页。

原因：`langgraph dev` 用的是系统 Python。那套 FastMCP 的 `Client` 没有 `mode` 参数。代码里写了 `mode="legacy"`，调用直接抛错。`search_notes` 把异常吞掉，返回空列表，于是只剩下 Tavily。

改法：不再用 FastMCP 客户端。按这个服务的要求直接 POST `initialize` 和 `tools/call`，只调用 `search_feeds` 和 `get_feed_detail`。失败时交回已经拿到的笔记，不挡住网页搜索。
