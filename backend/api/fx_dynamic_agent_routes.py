"""A2UI 汇率 Agent 路由 —— 支持普通文本消息与 A2UI action 回传。"""
import json
import logging

from a2ui.adk import SendA2uiToClientToolset, A2uiProcessorStore, A2uiInstruction, a2ui_rpc_callback
from a2ui.core.basic_catalog import BasicCatalog
from a2ui.inference_formats import DirectJsonFormatFactory
from a2ui.processor import A2uiRequestProcessor
from fastapi import APIRouter
from google.adk import Agent, Runner
from google.adk.sessions import DatabaseSessionService
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from agents import a2ui_policy
from tools.fx_tools import get_exchange_rate, convert_currency, get_account_balances, exchange_in_account
from utils.adk_utils import AdkUtils
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

    # App name
    APP_NAME = "fx_agent_app"


    _BUSINESS_INSTRUCTION = """你是一个汇率助手，帮助用户查询汇率、查看账户余额并进行货币兑换。

    工作流程：
    1. 用户询问汇率时（例如"美元兑人民币多少"），先调用 get_exchange_rate 获取汇率，
       同时调用 get_account_balances 读取账户余额；然后调用 send_a2ui_json_to_client
       把结果以 A2UI 呈现。
       UI 由你根据当轮需求自行设计：从 basic catalog 中自由选择合适的组件与布局
       （卡片、列表、表格、文本、输入框、按钮等任意组合），不要套用固定模板。
       展示内容至少要让用户看到汇率和账户余额；如果用户可能要继续兑换，提供金额输入
       与提交兑换的操作（action 按通用契约生成：name 用 exchange_in_account，
       context 用该工具的参数）。
    2. 用户询问账户或资产时，调用 get_account_balances 并用 A2UI 展示各币种余额。
    3. 当用户消息是 A2UI action JSON（包含 "action" 字段）时：
       按通用契约执行——action.name 对应工具名、action.context 对应工具参数
       （旧界面提交的 name=convert 等同于 exchange_in_account）。
       兑换成功后把对应界面更新为"兑换结果"状态，展示兑换前后的金额、所用汇率
       以及更新后的账户余额；工具返回 error（余额不足、金额非法、币种不支持）时，
       把错误原因展示在该界面，不要编造成功结果。
       兑换会改变账户余额，需按 surface 规则第 8 条同步所有展示余额的界面。
    4. 用户只想估算换算结果、明确表示不实际兑换时，调用 convert_currency（只读，不改余额）。
    5. 发送 UI 必须且只能通过 send_a2ui_json_to_client 工具调用，
       不要在回复文本中直接输出 A2UI JSON 或 a2ui-json 标签。
    6. 其它闲聊问题直接用简短文字回答，不要发送 UI。
    """

    processors = A2uiProcessorStore()

    def active_catalogs(ctx):
        processor = processors.get(ctx)
        return processor.active_catalogs if processor else None

    # Agent
    logger.info(f"processors.is_enabled: {processors.is_enabled}")
    root_agent = Agent(
        model=AdkUtils.get_adk_litellm_model(),
        name="fx_agent",
        description="An A2UI exchange-rate assistant.",
        # 业务 instruction + 通用 surface 规则；A2UI 格式提示词由 toolset 自动追加。
        # instruction=a2ui_policy.compose_instruction(_BUSINESS_INSTRUCTION),
        instruction=A2uiInstruction(
            a2ui_policy.compose_instruction(_BUSINESS_INSTRUCTION),
            a2ui_processor=processors.get,
            a2ui_enabled=processors.is_enabled,
        ),
        # 记录 surface 清单，并在每次请求前注入“已存在的界面 / action 目标”
        before_model_callback=a2ui_policy.inject_surface_context,
        after_tool_callback=a2ui_policy.record_surfaces,
        tools=[
            get_exchange_rate,
            convert_currency,
            get_account_balances,
            exchange_in_account,
            # SendA2uiToClientToolset(a2ui_enabled=True, a2ui_processor=a2ui_processor),
            SendA2uiToClientToolset(
                a2ui_enabled=processors.is_enabled,
                a2ui_processor=processors.get,
            )
        ],
        before_agent_callback=a2ui_rpc_callback(catalogs=active_catalogs),
    )

    # Runner：与 assistant_agent 共用同一个 SQLite 数据库（app_name 不同，互不影响）
    db_url = "sqlite+aiosqlite:///./my_agent_data.db"
    session_service = DatabaseSessionService(db_url=db_url)
    runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
    session = await session_service.get_session(app_name=APP_NAME, user_id=payload.user_id, session_id=payload.session_id)
    if session is None:
        session = await session_service.create_session(app_name=APP_NAME, user_id=payload.user_id, session_id=payload.session_id)

    _BASIC_CATALOG = BasicCatalog("v0.9")
    processor = A2uiRequestProcessor(
        catalogs=[_BASIC_CATALOG],
        format_factory=DirectJsonFormatFactory(),
    )
    processors.set(session.id, processor)


    stream_chat = ChatSse.stream_chat(
        message, runner, APP_NAME, payload.user_id, payload.session_id,
        a2ui_processor=processor,  # 兼容模型把 A2UI 直接写在文本里的情况
        state_delta=a2ui_policy.action_state_delta(payload.action),  # 本轮 action 的目标 surface
    )
    return StreamingResponse(stream_chat, media_type="text/event-stream")
