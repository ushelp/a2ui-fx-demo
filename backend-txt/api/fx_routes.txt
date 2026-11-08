"""A2UI 汇率 Agent 路由 —— 支持普通文本消息与 A2UI action 回传。"""
import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from agents import a2ui_policy, fx_agent
from utils.chat_sse import ChatSse

logger = logging.getLogger(__name__)

router = APIRouter()


class FxChatPayload(BaseModel):
    user_id: str = "ray"
    session_id: str = "fx_session"
    message: str | None = None  # 普通文本消息
    action: dict | None = None  # A2UI action（前端组件触发，name/surfaceId/context 等）


@router.post("/fx-agent", summary="Chat with the A2UI FX assistant")
async def chat(payload: FxChatPayload):
    # action 回传：包装成 A2UI client message JSON，作为本轮 user message 发给 agent
    if payload.action is not None:
        message = json.dumps({"version": "v0.9", "action": payload.action}, ensure_ascii=False)
        logger.info("A2UI action: %s", message)
    else:
        message = payload.message or ""

    stream_chat = ChatSse.stream_chat(
        message, fx_agent.runner, fx_agent.APP_NAME, payload.user_id, payload.session_id,
        a2ui_processor=fx_agent.a2ui_processor,  # 兼容模型把 A2UI 直接写在文本里的情况
        state_delta=a2ui_policy.action_state_delta(payload.action),  # 本轮 action 的目标 surface
    )
    return StreamingResponse(stream_chat, media_type="text/event-stream")
