from pathlib import Path
import os

from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 读取项目根目录的 .env，让下面的模型拿到 DEEPSEEK_API_KEY
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

os.environ.setdefault("LANGSMITH_TRACING", "true")
os.environ.setdefault("LANGSMITH_PROJECT", "travel-guide")

model = ChatDeepSeek(
    model="deepseek-v4-flash",
    extra_body={
        "thinking": {
            "type": "disabled"
        }
    }
)
