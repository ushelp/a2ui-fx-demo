"""A2UI 汇率 Agent 路由 —— 支持普通文本消息与 A2UI action 回传。"""
import json
import logging

from a2ui.core.schema import ProtocolVersion
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


    session_id = payload.session_id
    user_id = payload.user_id
    message = payload.message
    action = payload.action

    # action 回传：包装成 A2UI client message JSON，作为本轮 user message 发给 agent
    if action is not None:
        message = json.dumps({"version": ProtocolVersion.V1_0, "action": action}, ensure_ascii=False)
        logger.info("A2UI action: %s", message)
    else:
        message = payload.message or ""


    # 本轮 processor：先确定 session，再登记到 store（toolset 通过 processors.get /
    # processors.is_enabled 按 ctx.session.id 取回），流结束后移除。
    session = await ChatSse.get_or_create_session(
        fx_agent.runner, fx_agent.APP_NAME, user_id, session_id
    )
    processor = fx_agent.new_processor()
    fx_agent.processors.set(session.id, processor)

    async def frames():
        try:
            async for frame in ChatSse.stream_chat(
                    message,
                    fx_agent.runner,
                    fx_agent.APP_NAME,
                    user_id,
                    session_id,
                    a2ui_processor=processor,  # 兼容模型把 A2UI 直接写在文本里的情况
                    state_delta=a2ui_policy.action_state_delta(action),  # 本轮 action 的目标 surface
            ):
                yield frame
        finally:
            fx_agent.processors.remove(session.id)

    return StreamingResponse(frames(), media_type="text/event-stream")
