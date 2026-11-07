import json
import logging
from typing import AsyncGenerator

from google.adk import Runner
from google.adk.agents import RunConfig
from google.adk.agents._streaming_mode import StreamingMode
from google.genai import types

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ChatSse:
    @classmethod
    def _sse(cls, payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @classmethod
    async def stream_chat(cls,
                          message: str,
                          runner: Runner,
                          app_name: str,
                          user_id: str,
                          session_id: str | None = None,
                          ) -> AsyncGenerator[str, None]:

        # Session
        session = await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
        if session is None:
            session = await runner.session_service.create_session(app_name=app_name, user_id=user_id,
                                                                  session_id=session_id)

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
        )

        streamed = False  # 当前这一轮是否已经输出过增量文本
        try:
            async for event in events:
                logger.info(event)
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
                        yield cls._sse({"type": "chunk", "text": answer_text})
                else:
                    # final event：本轮 chunk 结束后，输出一次聚合的完整内容
                    if thought_text:
                        yield cls._sse({"type": "thought_full", "text": thought_text})
                    if answer_text:
                        yield cls._sse({"type": "full", "text": answer_text})
        except Exception as exc:
            yield cls._sse({"type": "error", "message": str(exc)})
        finally:
            yield cls._sse({"type": "done"})
