import dotenv
dotenv.load_dotenv()

import datetime
import uuid

# import litellm
#
# litellm.turn_on_debug()

from google.adk import Runner
from google.adk.agents import RunConfig
from google.adk.agents.llm_agent import Agent
from google.adk.agents.run_config import StreamingMode
from google.adk.models import LiteLlm
from google.adk.sessions import InMemorySessionService, DatabaseSessionService, Session

import warnings

from util.adk_util import AdkUtil

# Ignore all warnings
warnings.filterwarnings("ignore")

import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from google.genai import types


def get_current_time():
    """Returns the current time."""
    logger.info("Getting current time for user query")
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


async def create_agent():
    model = AdkUtil.get_adk_litellm_model()
    root_agent = Agent(
        model=model,
        name='root_agent',
        description='A helpful assistant for user questions.',
        instruction='''
        Answer user questions to the best of your knowledge and available tools: 
        - get_current_time: Returns the current time.
        ''',
        tools=[get_current_time]
    )
    logger.info(f"Agent created: {root_agent.name}")
    return root_agent


async def setup_session_and_runner(app_name: str, user_id: str, session_id: str) -> tuple[Session, Runner]:
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
    return session, runner


async def call_agent_async(query: str, runner: Runner, user_id: str, session_id: str):
    """Sends a query to the agent and prints the final response."""
    logger.info(f"\n>>> User Query: {query}")

    # Prepare the user's message in ADK format
    content = types.Content(role='user', parts=[types.Part(text=query)])

    config = RunConfig(
        # streaming_mode=StreamingMode.NONE,
        streaming_mode=StreamingMode.SSE,
        max_llm_calls=150
    )

    events = runner.run_async(user_id=user_id, session_id=session_id, new_message=content, run_config=config)

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
    APP_NAME = "ai_agent_app"
    USER_ID = "ray"
    SESSION_ID = f"session_{datetime.time().strftime('%H%M%S%f')}_{uuid.uuid4()}"  # Using a fixed ID for simplicity

    session, runner = await setup_session_and_runner(app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID)
    await call_agent_async(
        # "Hi, Who are you?",
        "Hi, 现在几点了?",
        runner=runner, user_id=USER_ID, session_id=SESSION_ID)


import asyncio

if __name__ == "__main__":
    try:
        asyncio.run(run_conversation())
    except Exception as e:
        logger.info(f"An error occurred: {e}")
