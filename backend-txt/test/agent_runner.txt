import dotenv

dotenv.load_dotenv()

import datetime
import uuid

# import litellm
#
# litellm.turn_on_debug()

from google.adk.agents import RunConfig
from google.adk.agents.run_config import StreamingMode

import warnings

# Ignore all warnings
warnings.filterwarnings("ignore")

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from google.genai import types

# from agents.assistant_agent import runner as assistant_agent_runner, APP_NAME as ASSISTANT_APP_NAME
from agents import assistant_agent


async def get_events(user_id, session_id, user_message):
    runner = assistant_agent.runner
    app_name = assistant_agent.APP_NAME

    # Session
    session = await runner.session_service.get_session(app_name=app_name, user_id=user_id, session_id=session_id)
    if session is None:
        session = await runner.session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    # Message
    new_message = types.Content(role="user", parts=[types.Part(text=user_message)])

    # Run config
    run_config = RunConfig(
        # streaming_mode=StreamingMode.NONE,
        streaming_mode=StreamingMode.SSE,
        max_llm_calls=150
    )

    # Run
    return runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=new_message,
        run_config=run_config,
    )


async def call_agent_async(query: str, user_id: str, session_id: str):
    """Sends a query to the agents and prints the final response."""
    logger.info(f"\n>>> User Query: {query}")

    events = await get_events(user_id=user_id, session_id=session_id, user_message=query)

    full_response_text = ""
    full_thought_text = ""
    async for event in events:
        logger.warning('-----Event-----')
        logger.warning(f"{event}")
        logger.warning(
            f"[Event] Author: {event.author}, Type: {type(event).__name__}, Final: {event.is_final_response()}, Content: {event.content}")

        if event.get_function_calls():
            logger.info(f'Function Calls: {event.get_function_calls()}')
        if event.get_function_responses():
            logger.info(f'Function Responses: {event.get_function_responses()}')

        # Accumulate streaming text if needed...
        if event.content and event.content.parts:
            full_thought_text += "".join(
                part.text for part in event.content.parts
                if (part.thought and part.text)
            )

        if event.is_final_response():
            logger.info("Final response received")
            if event.content and event.content.parts:
                full_response_text += "".join(
                    part.text for part in event.content.parts
                    if (not part.thought and part.text)
                )
            else:
                logger.info("No text in final response")

    logger.info(f"\n>>> User Query: {query}")
    logger.info(f"<<< Agent Thoughts: {full_thought_text}")
    logger.info(f"<<< Agent Response: {full_response_text}")


async def run_conversation():
    USER_ID = "ray"
    SESSION_ID = f"session_{datetime.time().strftime('%H%M%S%f')}_{uuid.uuid4()}"  # Using a fixed ID for simplicity

    await call_agent_async(
        # "Hi, Who are you?",
        "Hi, 现在几点了?", user_id=USER_ID, session_id=SESSION_ID)


import asyncio

if __name__ == "__main__":
    try:
        asyncio.run(run_conversation())
    except Exception as e:
        logger.info(f"An error occurred: {e}")
