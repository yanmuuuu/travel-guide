from langgraph.graph import MessagesState


"""1.定义状态"""
# messages 由 MessagesState 带入：用户和助手的回复都往这个列表追加
class GuideState(MessagesState):
    user_text: str          # 这一轮用户原话

    route: str              # plan 继续，note 补充条件，end 结束
    destination: str        # 目的地
    origin: str             # 出发地，没说就空字符串
    start_date: str         # 没写年份是「12月5日」，写了年份是 YYYY-MM-DD。没说就空字符串
    days: int               # 天数
    nights: int             # 住宿夜数，3天2夜里的 2
    hotel_needed: bool      # 要不要酒店
    transport_go: str       # 去程方式，没说就空字符串
    transport_back: str     # 回程方式，没说就空字符串
    budget_min: int         # 这一趟最低总预算（元）。用户没说先为 0，写稿时按平价折算
    budget_max: int         # 这一趟最高总预算（元）。用户没说先为 0，写稿时按平价折算
    adults: int             # 大人人数，没说就 0
    children: int           # 小孩人数，没说就 0
    seniors: int            # 老人人数，没说就 0

    msg_id: str             # 占位消息
    quick_take: str         # 快答
    sources: list           # 采集到的网页
    weather: str            # 按出发日期逐天查到的天气预报，没查到就空字符串
    guide: str              # 攻略正文
    critique: dict          # 自检结果
    rounds: int             # 已经重写或补搜的轮次
    feedback: str           # 这一轮要改的点
    preferences: dict       # 这次读到的长期偏好，只用来补充没说的内容
    notes: str              # 这一趟的补充条件，例如下雨可以打伞出门。不进长期偏好
    note_query: str         # 这一轮补充要用的那一条搜索
    note_hits: list         # 这一轮补充搜到的网页
    note_step: str          # 补充走到哪：judge、rewrite、keep 一类
