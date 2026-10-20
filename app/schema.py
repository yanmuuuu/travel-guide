from typing import Literal

from pydantic import BaseModel, Field


"""听懂需求时，模型要填的表"""
class plan_parse(BaseModel):
    is_plan: bool = Field(description="用户是不是在要求规划行程、攻略或出游安排。闲聊、问好、与旅行无关为 false")
    destination: str = Field(description="目的地，没有就空字符串。不要猜测")
    origin: str = Field(description="出发地，从哪里出发。没有就空字符串，不要把目的地填进来，也不要猜测")
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
    search_queries: list[str] = Field(description="需要补搜的查询，不需要就空列表")
