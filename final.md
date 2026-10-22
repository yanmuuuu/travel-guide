flowchart TD
  msg[用户的下一句] --> sw{点了深度思考吗}

  sw -->|没点| simple[固定的行程图]
  simple --> idea[一份思路：大概怎么玩，班次和房价标待核实]
  idea --> card[行程卡片：字段、对话、长期偏好]

  sw -->|点了| read[用同一个对话编号读出行程卡片]
  read --> agent[deep_agent]
  agent --> tools[自己决定调哪些 MCP 和技能：12306、酒店、天气、门票、景点]
  tools --> agent
  agent --> ready{衣食住行都具体并且定下来了吗}
  ready -->|还要人来选：车次、酒店、雨天改期、超出预算| ask[停下来问用户]
  ask --> agent
  ready -->|都确定了| full[生成最终行程：怎么来、怎么走、住哪、吃什么、花多少]
  full --> card
