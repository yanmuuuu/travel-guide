# travel-guide

不开深度推理时的行程攻略图。

## 文件

按租房助手的拆法，一张图三个文件：

| 文件 | 作用 |
| --- | --- |
| `app/state.py` | 这一趟要传递的字段，含对话 |
| `app/context.py` | 用户编号 |
| `app/schema.py` | 听懂需求和自检要填的表 |
| `app/node.py` | 节点和三个路由 |
| `app/graph.py` | 节点、边、编译。给 Studio 用，不自带记忆 |
| `app/store.py` | 长期偏好。测试用内存，本地运行才接上 |
| `app/llm.py` | DeepSeek，不开思考 |
| `app/mcp/search.py` | Tavily 联网搜索 |
| `app/mcp/weather.py` | 国内中国天气网，海外 Open-Meteo |
| `app/mcp/xhs.py` | 小红书笔记。没配地址就跳过 |
| `app/run.py` | 本地跑一次，带内存对话和偏好 |

深度推理不在这个仓库里。密钥放在本机 `.env`，字段见 `.env.example`。`.env` 不会进仓库。

在项目目录运行：

```powershell
python -m app.run
```
