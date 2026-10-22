# travel-guide

固定行程图，以及点了深度思考之后的 deep agent。

## 文件

```
app/
├── guide/      不开深度思考的固定行程图
├── deep/       点了深度思考之后的 deep agent
└── external/   两张图共用的外部查询
```

### `app/guide/` 固定行程图

按租房助手的拆法，一张图三个文件，外加模型、记忆和本地运行：

| 文件 | 作用 |
| --- | --- |
| `state.py` | 这一趟要传递的字段，含对话 |
| `context.py` | 用户编号 |
| `schema.py` | 听懂需求和自检要填的表 |
| `node.py` | 节点和三个路由 |
| `graph.py` | 节点、边、编译。给 Studio 用，不自带记忆 |
| `store.py` | 长期偏好。测试用内存，本地运行才接上 |
| `llm.py` | DeepSeek，不开思考 |
| `run.py` | 本地跑一次，带内存对话和偏好 |

### `app/deep/` 深度思考

| 文件 | 作用 |
| --- | --- |
| `llm.py` | 深度思考用的 DeepSeek。思考开关关掉，避免工具多轮断掉 |
| `tools.py` | 子代理的查询，按路线、交通、住宿、吃饭分组，外加主代理用来问人的 ask |
| `agent.py` | 主代理和 route、traffic、stay、food 四个子代理。先排路线，再沿路线安排其余 |
| `graph.py` | 编译好的深度思考图。给 Studio 用，不自带记忆 |
| `run.py` | 用同一段对话编号接着跑，停下来问人 |

### `app/external/` 外部查询

| 文件 | 作用 |
| --- | --- |
| `mcp_client.py` | 几路 MCP 共用的 HTTP 调用，本身不查数据 |
| `search.py` | Tavily 联网搜索 |
| `weather.py` | 国内中国天气网，海外 Open-Meteo |
| `xhs.py` | 小红书笔记。没配地址就跳过 |
| `rail.py` | 12306 余票和票价。没配本机地址就跳过 |
| `flight.py` | 飞常准机票票价。没配密钥就跳过 |
| `price.py` | 景点开放时间、票价、要不要提前买。不预约 |
| `hotel.py` | RollingGo 中文酒店每晚展示价。没配密钥时用 Pilot |
| `food.py` | 高德餐馆评分、人均和营业时间 |
| `amap.py` | 高德专属地图、路线、附近酒店和餐馆。没配密钥就跳过 |

深度思考用的是现在招聘里常见的 Deep Agents：主代理派子代理，子代理去查 MCP，要人选时用 LangGraph 打断。密钥放在本机 `.env`，字段见 `.env.example`。`.env` 不会进仓库。

在项目目录运行：

```powershell
python -m app.guide.run
python -m app.deep.run
```

看 Studio 用 `langgraph dev`，里面有 `travel_guide` 和 `deep_agent` 两张图。
