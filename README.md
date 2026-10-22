# travel-guide · 智能旅行规划助手

基于 **LangGraph** 和 **Deep Agents** 的多智能体旅行规划系统。用户用一句话说出行程需求，系统查实时的火车票、酒店房价、天气、景点和餐馆，排好每天的路线，生成一份可以直接照着走的行程：怎么去、怎么玩、住哪、吃什么、花多少。

## 两种模式

**快速模式**：一张固定流程的 LangGraph 状态图。依次听懂需求、联网和小红书检索、查天气、写攻略，再由模型自检。资料不够就补搜，写得不好就重写，最多三轮后定稿。会记住用户的长期偏好（出发地、口味、住宿习惯）。

**深度思考模式**：一个主代理指挥四个子代理，每个子代理只管一类工具，查完交回一份短报告，明细不进主对话。

| 子代理 | 负责 | 数据源 |
| --- | --- | --- |
| route | 天气、景点和开放时间、站间交通，按地理位置排出每天的路线并画成地图 | 高德、天气、小红书、Tavily |
| traffic | 往返火车（含中转）和航班 | 12306、飞常准 |
| stay | 每晚落脚点附近的酒店和每晚房价 | RollingGo、Pilot、高德 |
| food | 每顿饭前后那一站附近的餐馆和人均 | 高德、小红书 |

主代理先定路线，再并行派出交通、住宿、餐饮三个子代理。遇到需要用户拍板的事（选哪趟车、住哪家、下雨要不要改期、超预算怎么办），用 LangGraph 的 `interrupt` 停下来问，用户回答后接着跑。终稿之后用户追问时，主代理会重新派子代理去查，不靠已有资料硬答。

## 技术栈

- **智能体框架**：LangGraph（StateGraph、条件路由、`interrupt` / `Command(resume)` 人机协同、checkpointer 和 store 记忆）、Deep Agents（主代理加子代理、`task` 派发、中间件按代理收窄工具）
- **大模型**：DeepSeek（`langchain-deepseek`），工具调用加结构化输出
- **MCP**：自己写的 Streamable HTTP 客户端，按白名单只开放查询类工具，接入 12306、飞常准、RollingGo 酒店、Pilot 酒店、高德地图、小红书
- **外部接口**：高德 Web 服务 v3 和 v5（地点、周边、路线规划、专属地图）、Tavily 搜索、中国天气网和 Open-Meteo
- **可观测**：LangSmith 追踪，LangGraph Studio 调试两张图
- **工程**：Python 3.11、httpx 异步、Pydantic、python-dotenv

## 目录

```
app/
├── guide/      快速模式：固定流程的行程图
├── deep/       深度思考：主代理、子代理和工具
└── external/   两种模式共用的外部查询（MCP 和 REST）
```

## 运行

```powershell
pip install -e .
copy .env.example .env    # 填入各项密钥
python -m app.guide.run   # 快速模式
python -m app.deep.run    # 深度思考
langgraph dev             # 在 Studio 里看两张图
```
