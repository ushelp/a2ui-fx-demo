"""ADK Agent 与 Runner 配置。"""

from dotenv import load_dotenv
from google.adk.sessions import DatabaseSessionService

from tools.get_current_time import get_current_time

load_dotenv()

from google.adk.agents import Agent
from google.adk.runners import Runner

from utils.adk_utils import AdkUtils

# App name
APP_NAME = "ai_agent_app"

# Agent
root_agent = Agent(
    model=AdkUtils.get_adk_litellm_model(),
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='''
        Answer user questions to the best of your knowledge and available tools: 
        - get_current_time: Returns the current time.
        ''',
    tools=[get_current_time]
)

# Runner
# InMemoryRunner 自带内存版 SessionService，适合示例与本地调试
# runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)

# SQLite 持久化会话（需要安装 google-adk[db]，已包含 sqlalchemy + aiosqlite）
db_url = "sqlite+aiosqlite:///./my_agent_data.db"
session_service = DatabaseSessionService(db_url=db_url)
runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
