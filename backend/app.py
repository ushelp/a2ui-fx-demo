"""A2UI 餐厅助手 —— FastAPI 启动入口（含 CORS）。

启动方式：
    uv run python backend/app.py
或：
    uv run uvicorn backend.app:app --reload
"""
import dotenv
dotenv.load_dotenv()

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.user import router as user_router

app = FastAPI(title="A2UI FX Demo Backend", version="0.1.0")

# CORS 中间件

# 前端 Origin 白名单，可通过环境变量 CORS_ORIGINS 覆盖（逗号分隔）
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,        # 前端 Origin 白名单
    allow_credentials=True,            # 允许携带 Cookie
    allow_methods=["*"],               # 允许所有 HTTP 方法
    allow_headers=["*"],               # 允许所有请求头
)


@app.get("/api/health")
async def health() -> dict:
    """健康检查，同时可用于验证 CORS 是否生效。"""
    return {"status": "ok", "origins": CORS_ORIGINS}


# 用户模块路由
app.include_router(user_router, prefix="/api", tags=["users"])


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.app:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
