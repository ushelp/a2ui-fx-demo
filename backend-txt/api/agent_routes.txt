"""用户模块路由 —— 提供用户相关的 API（当前为 mock 数据）。"""
import logging
from starlette.responses import StreamingResponse

from utils.chat_sse import ChatSse

logger = logging.getLogger(__name__)
from fastapi import APIRouter
from pydantic import BaseModel
from agents import assistant_agent

router = APIRouter()


class ChatPayload(BaseModel):
    user_id: str = "ray"
    session_id: str = "session_id"
    message: str


@router.post("/assistant-agent", summary="Chat with the AI assistant")
async def chat(chat_payload: ChatPayload):
    # Agent
    runner = assistant_agent.runner
    app_name = assistant_agent.APP_NAME

    # User request
    user_id = chat_payload.user_id
    session_id = chat_payload.session_id
    message = chat_payload.message

    # Call Agent
    stream_chat = ChatSse.stream_chat(message, runner, app_name, user_id, session_id)

    return StreamingResponse(stream_chat, media_type="text/event-stream")
