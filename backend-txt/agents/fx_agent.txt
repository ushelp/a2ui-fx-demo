"""A2UI 汇率助手 —— ADK + a2ui-adk 集成的 Agent 与 Runner 配置。

集成要点（参考 a2ui-adk README）：
- A2uiRequestProcessor：持有 basic catalog（v0.9）与推理格式（Direct JSON），
  负责生成 A2UI 系统提示词、编译并校验模型输出的 A2UI JSON。
- A2uiProcessorStore：按 session 保存该会话的 processor。ADK session state 会被
  JSON 序列化并在每次加载时复制，processor（及其可能绑定的 RendererState）不能
  放进 session state，所以放在内存 store 里，由路由在每轮请求开始 set、结束 remove。
- SendA2uiToClientToolset：向模型暴露 `send_a2ui_json_to_client` 工具，
  模型通过工具调用把 A2UI 发给前端；校验后的消息在工具响应的
  `validated_a2ui_json` 字段中，由 SSE 层拦截转发（见 utils/chat_sse.py）。
  该 toolset 会在 `process_llm_request` 时自动把 A2UI 提示词（规则 +
  catalog schema + 工具说明）追加到系统指令里，因此不需要
  再叠加 `A2uiInstruction`（后者是给“把 A2UI 写在文本回复里”的 agent 用的）。

本示例不提供 few-shot 示例：UI 由模型在每轮根据用户意图、从 basic catalog 的
组件里自由组合生成，避免示例变成固定模板限制后续功能扩展。格式正确性由
catalog schema 与 `A2uiRequestProcessor` 的校验保证。
"""
from dotenv import load_dotenv
from google.adk.sessions import DatabaseSessionService

from tools.fx_tools import convert_currency, exchange_in_account, get_account_balances, get_exchange_rate

load_dotenv()

from google.adk.agents import Agent
from google.adk.runners import Runner

from a2ui.adk import A2uiProcessorStore, SendA2uiToClientToolset
from a2ui.core.basic_catalog import BasicCatalog
from a2ui.inference_formats.direct_json import DirectJsonFormatFactory
from a2ui.processor import A2uiRequestProcessor

from utils.adk_utils import AdkUtils
from agents import a2ui_policy

# App name
APP_NAME = "fx_agent_app"

# A2UI basic catalog（v0.9）
_BASIC_CATALOG = BasicCatalog("v0.9")

# A2UI 请求处理器：每个请求一个实例，由路由放入 processors store。
# processor 不绑定 RendererState（chat 型 agent），但仍按 session 隔离，
# 便于后续接入状态化能力时不需要再改结构。
# 不传 examples：让模型自由组合 UI，而不是照着固定示例套模板。
processors = A2uiProcessorStore()


def new_processor() -> A2uiRequestProcessor:
    """创建单个请求/会话使用的 A2UI 处理器。"""
    return A2uiRequestProcessor(
        catalogs=[_BASIC_CATALOG],
        format_factory=DirectJsonFormatFactory(),
    )


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

# Agent
root_agent = Agent(
    model=AdkUtils.get_adk_litellm_model(),
    name="fx_agent",
    description="An A2UI exchange-rate assistant.",
    # 业务 instruction + 通用 surface 规则；A2UI 格式提示词由 toolset 自动追加。
    instruction=a2ui_policy.compose_instruction(_BUSINESS_INSTRUCTION),
    # 记录 surface 清单，并在每次请求前注入“已存在的界面 / action 目标”
    before_model_callback=a2ui_policy.inject_surface_context,
    after_tool_callback=a2ui_policy.record_surfaces,
    tools=[
        get_exchange_rate,
        convert_currency,
        get_account_balances,
        exchange_in_account,
        SendA2uiToClientToolset(a2ui_enabled=processors.is_enabled, a2ui_processor=processors.get),
    ],
)

# Runner：与 assistant_agent 共用同一个 SQLite 数据库（app_name 不同，互不影响）
db_url = "sqlite+aiosqlite:///./my_agent_data.db"
session_service = DatabaseSessionService(db_url=db_url)
runner = Runner(agent=root_agent, app_name=APP_NAME, session_service=session_service)
