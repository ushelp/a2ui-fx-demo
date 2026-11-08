"""通用 A2UI Surface 管理策略（与业务无关）。

所有接入 A2UI 的 Agent 共用这一份策略，业务 instruction 只描述业务本身：

- SURFACE_RULES：通用的 surface 生命周期规则，由 compose_instruction 拼进业务指令
- record_surfaces：A2UI 工具调用成功后，把当前 surface 清单写入 session state
- inject_surface_context：每次请求模型前，把 surface 清单 / action 目标追加到系统指令
- action_state_delta：把本轮 action 的 surfaceId 写入 session state（路由层使用）
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

from a2ui.constants import (
    A2UI_CREATE_SURFACE_KEY,
    A2UI_DELETE_SURFACE_KEY,
    A2UI_SURFACE_ID_KEY,
    A2UI_TOOL_NAME,
    A2UI_VALIDATED_JSON_KEY,
)
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

SURFACES_STATE_KEY = "a2ui_surfaces"
# temp: 前缀表示仅在本轮 invocation 有效，避免“上轮是 action”的提示残留到后续对话
LAST_ACTION_SURFACE_KEY = "temp:a2ui_last_action_surface"

ACTION_RULES = """\
## A2UI Action 契约（通用）
1. action.name 使用能力名，并尽量与真实工具名一致（如 exchange_in_account）；
   同一个能力在不同界面里必须使用同一个名字。
2. action.context 的 key 与对应工具的参数名一致（如 amount / base_currency / quote_currency），
   值用 {"path": "..."} 绑定到界面输入，由渲染器在提交时替换为实际值。
3. 收到 action 时，先把 action.name 与可用工具名对齐：同名工具直接把 action.context
   作为参数执行（类型按工具声明做必要转换），不要再为字段名做额外翻译。
4. 框架级动作（如 close_surface）不是工具，按 surface 规则处理。
"""

SURFACE_RULES = """\
## A2UI Surface 管理规则（通用）
1. 一个 surface 对应一个业务实例（一次查询 / 一笔操作 / 一个对象），不是一条回复。
2. 同一实例内的状态推进一律更新该 surface：仅数据变化用 updateDataModel，
   组件结构变化用 updateComponents。
3. 只有开始新的独立实例时才 createSurface；id 要稳定、语义化（如 <domain>-<object>），
   不要用递增序号或时间戳。
4. 收到 A2UI action 时，必须复用 action.surfaceId 指向的 surface；
   除非用户明确要求另开界面或并排对比，否则禁止新建。
5. 多个对象需要并存时，每个实例一个 surface；不要把一个实例拆成多个重复界面。
6. 任务结束或界面失效时才 deleteSurface；不要用新建 surface 来“刷新”。
7. 每条 A2UI 消息只包含一个操作；组件与布局仍由你自由设计，本规则不限制设计。
8. 如果本轮操作改变了共享业务数据（如账户余额），并且已有其他 surface 也在展示该数据，
   必须同步更新那些 surface：优先把多条消息放进同一次 send_a2ui_json_to_client 调用；
   需要时也可以在同一轮继续调用该工具。工具返回 validated_a2ui_json 只表示已发送成功，
   不要重复发送同一界面。
9. 收到 name 为 close_surface 的 action 时，说明用户已经关闭该界面：
   用 send_a2ui_json_to_client 发送一条 deleteSurface（surfaceId = action.surfaceId），
   不要重新创建或再次更新它；之后也不要再往这个 surfaceId 发消息。
"""


def compose_instruction(business_instruction: str) -> str:
    """在业务 instruction 后追加通用 action 契约与 surface 规则。"""
    return f"{business_instruction.rstrip()}\n\n{ACTION_RULES}\n{SURFACE_RULES}"


def action_state_delta(action: dict[str, Any] | None) -> dict[str, str]:
    """提取 action 的 surfaceId，作为本轮运行的 state_delta。"""
    surface_id = (action or {}).get(A2UI_SURFACE_ID_KEY)
    return {LAST_ACTION_SURFACE_KEY: surface_id} if surface_id else {}


def record_surfaces(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """A2UI 工具成功后，把 surface 清单记到 session state，供下一轮注入。"""
    if tool.name != A2UI_TOOL_NAME:
        return None
    messages = (tool_response or {}).get(A2UI_VALIDATED_JSON_KEY)
    if not isinstance(messages, list):
        return None

    surfaces: dict[str, str] = dict(tool_context.state.get(SURFACES_STATE_KEY) or {})
    now = datetime.datetime.now().strftime("%H:%M:%S")
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if A2UI_CREATE_SURFACE_KEY in msg:
            sid = (msg[A2UI_CREATE_SURFACE_KEY] or {}).get(A2UI_SURFACE_ID_KEY)
            if sid:
                surfaces[sid] = now
            continue
        if A2UI_DELETE_SURFACE_KEY in msg:
            sid = (msg[A2UI_DELETE_SURFACE_KEY] or {}).get(A2UI_SURFACE_ID_KEY)
            surfaces.pop(sid, None)
            continue
        for op in ("updateComponents", "updateDataModel"):
            if op in msg:
                sid = (msg[op] or {}).get(A2UI_SURFACE_ID_KEY)
                if sid and sid in surfaces:
                    surfaces[sid] = now
                break

    tool_context.state[SURFACES_STATE_KEY] = surfaces

    # SDK 的 A2UI 工具默认设置 skip_summarization=True（避免多一次推理），
    # 但在 ADK 里这会让该 function-response 事件被 is_final_response() 判为最终事件，
    # 直接结束本轮（见 google/adk/flows/llm_flows/base_llm_flow.py 的 run_async 循环）。
    # 结果是模型无法在同一轮继续发送“另一个需要同步的 surface”。
    # 这里改回 False：工具 description 本身也声明“同一轮可以多次调用”。
    tool_context.actions.skip_summarization = False
    return None


def inject_surface_context(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[LlmResponse]:
    """把当前 surface 清单 / action 目标追加到系统指令末尾。

    追加时机在 SendA2uiToClientToolset 写入 A2UI 格式提示词之后，
    因此这段“离请求最近”的状态说明最容易被执行。
    """
    surfaces = callback_context.state.get(SURFACES_STATE_KEY) or {}
    last_action = callback_context.state.get(LAST_ACTION_SURFACE_KEY)

    lines = []
    if surfaces:
        existing = "、".join(sorted(surfaces))
        lines.append(f"已存在的 surfaceId：{existing}。同一业务实例请复用，不要重复 createSurface。")
    if last_action:
        lines.append(f"本轮是 action 回包，必须复用 surfaceId={last_action}，不要新建 surface。")
    if not lines:
        return None

    llm_request.append_instructions(["\n".join(["## 当前 A2UI 会话状态", *lines])])
    return None
