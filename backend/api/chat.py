"""用户模块路由 —— 提供用户相关的 API（当前为 mock 数据）。"""
from typing import AsyncGenerator
from urllib.request import Request
import logging

from google.adk import Agent, Runner, Event
from google.adk.agents import RunConfig
from google.adk.agents._streaming_mode import StreamingMode
from google.adk.sessions import DatabaseSessionService, Session
from google.genai import types
from starlette.responses import StreamingResponse

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
from fastapi import APIRouter
from pydantic import BaseModel
from util.adk_util import AdkUtil

router = APIRouter()


class ChatPayload(BaseModel):
    app_name: str = "chat_app"
    user_id: str = "ray"
    session_id: str = "session_1"
    user_message: str


async def create_agent():
    model = AdkUtil.get_adk_litellm_model()
    root_agent = Agent(
        model=model,
        name='root_agent',
        description='A helpful assistant for user questions.',
        instruction='''
        Answer user questions to the best of your knowledge.
        '''
    )
    logger.info(f"Agent created: {root_agent.name}")
    return root_agent


async def get_events(app_name: str, user_id: str, session_id: str, user_message: str) -> AsyncGenerator[Event, None]:
    root_agent = await create_agent()

    # session_service = InMemorySessionService()
    db_url = "sqlite+aiosqlite:///./my_agent_data.db"
    session_service = DatabaseSessionService(db_url=db_url)
    session = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    if not session:
        session = await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    runner = Runner(agent=root_agent,  # The agent we want to run
                    app_name=app_name,  # Associates runs with our app
                    session_service=session_service,  # Uses our session manager
                    )
    logger.info(f"Session created: App='{app_name}', User='{user_id}', Session='{session_id}'")
    # Prepare the user's message in ADK format
    content = types.Content(role='user', parts=[types.Part(text=user_message)])

    config = RunConfig(
        # streaming_mode=StreamingMode.NONE,
        streaming_mode=StreamingMode.SSE,
        max_llm_calls=150
    )
    events = runner.run_async(user_id=user_id, session_id=session_id, new_message=content, run_config=config)
    return events


@router.get("/chat", summary="Chat with the bot")
async def chat(request: Request, chat_payload: ChatPayload):
    app_name = chat_payload.app_name
    user_id = chat_payload.user_id
    session_id = chat_payload.session_id
    user_message = chat_payload.user_message



    async def event_generator():
        events = await get_events(app_name=app_name, user_id=user_id, session_id=session_id,
                                  user_message=user_message)
        async for event in events:
            yield event

    return StreamingResponse(event_generator(), media_type="text/event-stream")
