from pathlib import Path
import os

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 和固定行程图共用项目根目录的 .env。
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

os.environ.setdefault("LANGSMITH_TRACING", "true")
os.environ.setdefault("LANGSMITH_PROJECT", "travel-guide")

# 模型思考关掉。DeepSeek 开着思考时，下一轮工具调用必须把 reasoning_content 原样送回，
# 当前客户端只存不送，多轮工具会断。深度思考指的是下面这张带子代理的图，不是这个开关。
model = ChatDeepSeek(
    model="deepseek-v4-flash",
    extra_body={
        "thinking": {
            "type": "disabled"
        }
    }
)
