import datetime
import json
import logging
from typing import AsyncGenerator

from a2ui.constants import A2UI_TOOL_NAME, A2UI_VALIDATED_JSON_KEY
from a2ui.parser.messages import to_message_dicts
from a2ui.parser.response_part import A2uiPart, TextPart
from a2ui.processor import A2uiRequestProcessor
from google.adk import Runner
from google.adk.agents import RunConfig
from google.adk.agents._streaming_mode import StreamingMode
from google.genai import types

logger = logging.getLogger(__name__)


class ChatSse:
    @classmethod
    def _sse(cls, payload: dict) -> str:
        payload['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @classmethod
    async def get_or_create_session(cls,
                                    runner: Runner,
                                    app_name: str,
                                    user_id: str,
                                    session_id: str | None = None):
        session = await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if session is None:
            session = await runner.session_service.create_session(app_name=app_name, user_id=user_id,
                                                                  session_id=session_id)
        return session

    @classmethod
    async def stream_chat(cls,
                          message: str,
                          runner: Runner,
                          app_name: str,
                          user_id: str,
                          session_id: str | None = None,
                          a2ui_processor: A2uiRequestProcessor | None = None,
                          state_delta: dict | None = None,
                          ) -> AsyncGenerator[str, None]:

        # Session
        session = await cls.get_or_create_session(runner, app_name, user_id, session_id)

        # Message
        new_message = types.Content(role="user", parts=[types.Part(text=message)])

        # Run config
        run_config = RunConfig(
            # streaming_mode=StreamingMode.NONE,
            streaming_mode=StreamingMode.SSE,
            max_llm_calls=150
        )

        # Run
        events = runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=new_message,
            run_config=run_config,
            state_delta=state_delta,
        )

        acc_text = ""  # 本轮累计的答案文本（用于探测内联 A2UI）
        suppress_chunks = False  # 已进入内联 <a2ui-json> 块后，停止推 chunk 帧
        try:
            async for event in events:
                logger.debug(
                    "event author=%s partial=%s tool_calls=%s tool_responses=%s final=%s",
                    event.author,
                    event.partial,
                    [fc.name for fc in event.get_function_calls()] or None,
                    [fr.name for fr in event.get_function_responses()] or None,
                    event.is_final_response(),
                )

                # 工具调用 / 工具结果：与文本帧独立，单独透传给前端。
                # 注意：仅在非 partial 事件上取，避免流式参数碎片被重复推送。
                if not event.partial:
                    for fc in event.get_function_calls():
                        yield cls._sse({
                            "type": "tool_call",
                            "id": fc.id,
                            "name": fc.name,
                            "args": fc.args or {},
                        })
                    for fr in event.get_function_responses():
                        # A2UI 工具响应：取出校验后的 A2UI 消息，以 a2ui 帧发给前端渲染
                        if fr.name == A2UI_TOOL_NAME and isinstance(fr.response, dict):
                            a2ui_messages = fr.response.get(A2UI_VALIDATED_JSON_KEY)
                            if a2ui_messages:
                                yield cls._sse({"type": "a2ui", "messages": a2ui_messages})
                                continue
                        yield cls._sse({
                            "type": "tool_result",
                            "id": fr.id,
                            "name": fr.name,
                            "resp": fr.response or {},
                        })

                if not event.content or not event.content.parts:
                    continue
                thought_text = "".join(
                    part.text
                    for part in event.content.parts
                    if part.text and part.thought
                )
                answer_text = "".join(
                    part.text
                    for part in event.content.parts
                    if part.text and not part.thought
                )
                if not answer_text and not thought_text:
                    continue

                if event.partial:
                    # 增量 chunk：逐步输出
                    if thought_text:
                        yield cls._sse({"type": "thought", "text": thought_text})
                    if answer_text:
                        # A2UI 场景：模型一旦开始输出 <a2ui-json> 标签，后续 chunk 是 UI JSON，
                        # 不再作为文本推给前端（最终统一由 a2ui 帧承载）
                        acc_text += answer_text
                        # if a2ui_processor is not None and "<a2ui-json>" in acc_text:
                        #     suppress_chunks = True
                        if not suppress_chunks:
                            yield cls._sse({"type": "chunk", "text": answer_text})
                else:
                    # final event：本轮 chunk 结束后，输出一次聚合的完整内容
                    if thought_text:
                        yield cls._sse({"type": "thought_full", "text": thought_text})
                        thought_text = ""
                    if answer_text:
                        # A2UI 场景：模型直接把 UI 写在文本里（未走工具）时，解析 <a2ui-json> 块，
                        # UI 部分转 a2ui 帧，其余文本仍按 full 帧输出
                        if a2ui_processor is not None and "<a2ui-json>" in answer_text:
                            yield cls._sse({"type": "a2ui_json", "text": answer_text})
                            try:
                                logger.debug("parsing inline A2UI: %s", answer_text)
                                parts = a2ui_processor.parse_response(answer_text, wrapped=True)
                            except Exception:
                                logger.exception("failed to parse inline A2UI, fallback to text")
                                parts = [TextPart(text=answer_text)]
                            for part in parts:
                                if isinstance(part, A2uiPart):
                                    logger.debug("parsed A2UI: %s", part.a2ui)
                                    yield cls._sse({"type": "a2ui", "messages": to_message_dicts(part.a2ui)})
                                elif isinstance(part, TextPart) and part.text.strip():
                                    logger.debug("parsed A2UI text: %s", part.text)
                                    yield cls._sse({"type": "full", "text": part.text})
                        else:
                            logger.debug("parsed text: %s", answer_text)
                            yield cls._sse({"type": "full", "text": answer_text})
                        answer_text = ""
        except Exception as exc:
            yield cls._sse({"type": "error", "message": str(exc)})
        finally:
            yield cls._sse({"type": "done"})
