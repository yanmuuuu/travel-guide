from typing import Literal

from pydantic import BaseModel, Field


"""听懂需求时，模型要填的表"""
class plan_parse(BaseModel):
    intent: Literal["plan", "note", "end"] = Field(description="plan：要求规划或修改行程字段。note：给已有行程加条件，例如可以接受打伞出去玩。end：闲聊、问好、与这次行程无关")
    note: str = Field(description="intent 为 note 时，把这一句收成一句约束，例如下雨可以打伞出门。否则空字符串")
    note_query: str = Field(description="intent 为 note 时的一条短搜索，否则空字符串。只写目的地加一件事，不要写日期，不要把用户原话整句放进去。可以打伞出门写成「武汉 雨天 户外」或「武汉 小雨 步行」。要找室内才写成「武汉 下雨天 室内」")
    destination: str = Field(description="目的地，没有就空字符串。不要猜测")
    origin: str = Field(description="这一趟的出发地。这一句改了就写新的城市名。从家里走、人在杭州、杭州出发，都写成杭州。这一句没提就沿用上一轮，上一轮也没有就空字符串，不要把目的地填进来")
    start_year: int = Field(description="出发年份。用户说出四位年份才填，例如 2026。只说月日、29号、今天、明天、下周三都填 0，不要自行补年份")
    start_month: int = Field(description="出发月份，1 到 12。只说 29号时用对话里已经有的月份。这一句改了日子就改月份。这一句和之前都没有日期就填 0")
    start_day: int = Field(description="出发日，1 到 31。这一句改了日子就用新的，例如已是 10月22日、这一句说 29号，填 29，不要留 22。没有日期就填 0")
    stated_origin: str = Field(description="只有这一句表达了出发地才填城市名。从家里走、人在杭州都写成杭州。这一句没表达就空字符串，不要把上一轮沿用的出发地填进来")
    stated_transport_go: str = Field(description="只有这一句表达了去程方式才填，如高铁、飞机、自驾。坐火车过去写成高铁。这一句没表达就空字符串，不要猜班次，也不要把上一轮沿用的方式填进来")
    stated_transport_back: str = Field(description="只有这一句表达了回程方式才填。回程也这样，就写成和去程相同的方式。这一句没表达就空字符串，不要照抄去程")
    days: int = Field(description="出行天数。3天2夜填 3。用户没说就填 1")
    nights: int = Field(description="住宿夜数。3天2夜填 2。只说几天并且要住宿时，夜数=天数-1。当天来回或不住宿填 0")
    hotel_needed: bool = Field(description="夜数大于 0，或用户要求安排住宿，则为 true")
    transport_go: str = Field(description="去程方式，如高铁、飞机、自驾。没说就空字符串，不要猜班次")
    transport_back: str = Field(description="回程方式。没说就空字符串，不要照抄去程")
    budget_min: int = Field(description="这一趟最低总预算，单位元，含住宿和交通。只说一个上限时填 0。没说就填 0")
    budget_max: int = Field(description="这一趟最高总预算，单位元。只说一个数时填这个数。没说就填 0")
    adults: int = Field(description="大人人数。没说就填 0，不要默认成 1")
    children: int = Field(description="小孩人数。没说就填 0")
    seniors: int = Field(description="老人人数。没说就填 0")
    taste: str = Field(description="这一轮说出的口味偏好。没说就空字符串")
    allergy: str = Field(description="这一轮说出的忌口或过敏。没说就空字符串")
    interest: str = Field(description="这一轮说出的兴趣，如自然、博物馆。没说就空字符串")
    pace: str = Field(description="这一轮说出的节奏，如一天不要太赶。没说就空字符串")
    budget_habit: str = Field(description="预算档次，如平价、中档、高档。只说具体金额时留空，不要把金额写进来")
    lodging: str = Field(description="这一轮说出的住宿偏好，如安静、要电梯。不要写某一家酒店名。没说就空字符串")
    companions: str = Field(description="通常和谁走，如同学、父母。不要写成这一次的人数。没说就空字符串")
    planning_habit: str = Field(description="规划习惯，如先订酒店再排景点。没说就空字符串")


"""自检时，模型要填的表。用来决定定稿、补搜还是重写。"""
class guide_critique(BaseModel):
    ok: bool = Field(description="这份攻略是否可以给定稿")
    action: Literal["none", "research", "rewrite"] = Field(description="通过填 none；缺资料填 research；资料够但写得不好填 rewrite")
    issues: list[str] = Field(description="问题列表，没有就空列表")
    search_queries: list[str] = Field(description="需要补搜的短查询，不需要就空列表。每条只写目的地加一件事，不要写日期，不要写整句原话。雨天仍要出门用「目的地 雨天 户外」或「目的地 小雨 步行」。找室内用「目的地 下雨天 室内」")


"""补充条件搜完之后，模型要填的表。决定要不要改已有行程。"""
class note_fit(BaseModel):
    useful: bool = Field(description="新摘录能否补上已有行程：改某天去哪，或按这句话把雨天安排从室内改成户外。只是重复已有景点，或与这句话无关，为 false")
