# Backend 架构笔记（Google ADK + FastAPI + SSE）

> 供后续任务快速参考。最后更新：2026-11-07

## 1. 目录结构

```
backend/
├── app.py                     # FastAPI 入口：日志配置、CORS、/api/health、挂载路由
├── api/
│   ├── agent_routes.py        # POST /api/assistant-agent（SSE 流式聊天）
│   ├── fx_routes.py           # POST /api/fx-agent（A2UI 汇率助手，SSE + action 回传）
│   └── user_routes.py         # GET  /api/users（mock 示例）
├── agents/
│   ├── a2ui_policy.py        # 通用 A2UI surface 策略（规则拼装 + 状态注入回调）
│   ├── assistant_agent.py     # root_agent + Runner 模块级单例
│   └── fx_agent.py            # A2UI 汇率 agent（a2ui-adk 集成）
├── utils/
│   ├── adk_utils.py           # AdkUtils.get_adk_litellm_model() 构造 LiteLlm
│   └── chat_sse.py            # 核心：ADK event 流 → SSE（含 a2ui 帧）
├── tools/
│   ├── get_current_time.py    # 示例工具
│   └── fx_tools.py            # mock 汇率 + 账户工具（get_exchange_rate / convert_currency / get_account_balances / exchange_in_account）
├── test/                      # 命令行直连 agent 的调试脚本
└── my_agent_data.db           # SQLite 会话持久化文件

frontend/                      # Vite + React 18 + TS（@a2ui/react v0.9 渲染器）
├── src/api.ts                 # SSE POST 读取与帧分发
├── src/App.tsx                # 聊天 + MessageProcessor + <A2uiSurface> + action 回传
└── vite.config.ts             # /api 代理到 127.0.0.1:8000
```

## 2. Agent 定义（agents/assistant_agent.py）

- `APP_NAME = "ai_agent_app"`，路由层会通过 `assistant_agent.APP_NAME` 引用。
- `root_agent = Agent(model=LiteLlm, name='root_agent', instruction=..., tools=[get_current_time])`。
- **模块导入时即创建单例** `runner`，路由直接复用，避免每请求重建。
- 会话持久化：`DatabaseSessionService(db_url="sqlite+aiosqlite:///./my_agent_data.db")`
  - ⚠️ db 路径是**启动时 cwd** 相对路径，不是 backend 目录。

## 3. 模型配置（utils/adk_utils.py）

- 默认本地 Ollama：`ollama_chat/qwen3.8:27b-mlx` @ `http://localhost:11434`
- 环境变量切换：`LLM_MODEL` / `LLM_API_BASE` / `LLM_API_KEY`
- `ollama` 开头模型会附加 `api_base` 与 `thinking=False`；其他 provider 附 `api_base`/`api_key`
- ⚠️ 已知小 bug：第 18–19 行 fallback 写反（`LLM_MODEL` 缺省回落到 base_url，反之亦然）。两个环境变量同时设或同时不设时不暴露。

## 4. FastAPI 路由

`app.py`：
- 加载 `.env`，配置 CORS（默认 `http://localhost:5173,http://127.0.0.1:5173`，可用 `CORS_ORIGINS` 覆盖）
- `GET /api/health`
- `app.include_router(user_router, prefix="/api")`
- `app.include_router(agent_routes, prefix="/api")`
- 启动：`uv run python backend/app.py` 或 `uvicorn backend.app:app --reload`，默认 `127.0.0.1:8000`

`api/agent_routes.py`：
- `ChatPayload { user_id="ray", session_id="session_id", message: str }`
- 处理函数仅一行核心：把 `ChatSse.stream_chat(...)` 包成 `StreamingResponse(..., media_type="text/event-stream")`

## 5. 流式输出核心（utils/chat_sse.py）

调用顺序：

1. `runner.session_service.get_session(app_name, user_id, session_id)` → None 则 `create_session(...)`
2. 构造 `types.Content(role="user", parts=[types.Part(text=message)])`
3. `RunConfig(streaming_mode=StreamingMode.SSE, max_llm_calls=150)`
4. `events = runner.run_async(user_id, session_id, new_message, run_config)`
5. 遍历事件，按 `event.partial` × `part.thought` 分类输出

### SSE 帧协议（每帧 `data: {json}\n\n`）

| type           | 条件                                       | 含义                 |
|----------------|--------------------------------------------|----------------------|
| `thought`      | `event.partial == True` 且 `part.thought`  | 增量思考片段         |
| `thought_full` | `event.partial == False` 且 `part.thought` | 本轮聚合完整思考     |
| `chunk`        | `event.partial == True` 且非 thought       | 增量答案片段         |
| `full`         | `event.partial == False` 且非 thought      | 本轮聚合完整答案     |
| `tool_call`    | `event.get_function_calls()` 非空（仅非 partial 事件，避免流式参数碎片重复） | `{"type":"tool_call","id":...,"name":...,"args":{...}}` |
| `tool_result`  | `event.get_function_responses()` 非空（仅非 partial） | `{"type":"tool_result","id":...,"name":...,"resp":{...}}` |
| `a2ui`         | A2UI 工具响应含 `validated_a2ui_json`，或 final 文本内联 `<a2ui-json>` 被解析成功 | `{"type":"a2ui","messages":[...]}`，直接喂给前端 MessageProcessor |
| `error`        | 捕获异常                                   | `{"type":"error","message":...}` |
| `done`         | `finally` 必发                             | 流结束标记           |

前端通常：用 `chunk` / `thought` 做打字机，用 `full` / `thought_full` 做最终覆盖，用 `tool_call` / `tool_result` 渲染工具调用过程（可通过 `id` 配对），用 `a2ui` 帧驱动 MessageProcessor 渲染 UI，用 `done` 关闭 Loading。

## 5.1 A2UI 汇率 Agent（agents/fx_agent.py + api/fx_routes.py）

集成方式（a2ui-adk 0.2.0，非 A2A）：
- `A2uiRequestProcessor(catalogs=[BasicCatalog("v0.9")], format_factory=DirectJsonFormatFactory())`：生成 A2UI 提示词、编译校验模型输出。不传 `examples`，UI 由模型自由组合，避免示例变成固定模板；格式正确性由 catalog schema + 校验兜底。
- `SendA2uiToClientToolset(a2ui_enabled=True, a2ui_processor=processor)`：向模型暴露 `send_a2ui_json_to_client` 工具；校验后的消息在工具响应 `validated_a2ui_json` 字段，由 `chat_sse.py` 拦截转 `a2ui` 帧。toolset 会自动追加 A2UI 提示词，无需再叠加 `A2uiInstruction`。
- 通用 surface 策略（agents/a2ui_policy.py，与业务无关，可被任何 A2UI agent 复用）：
  - `compose_instruction(business)`：业务 instruction + `ACTION_RULES` + `SURFACE_RULES`。`ACTION_RULES` 定义 action 契约：`action.name` 对齐工具名、`action.context` 的 key 对齐工具参数名、同名工具直接用 context 执行；业务 instruction 因此不再需要写具体字段名。
  - `SURFACE_RULES`：一个 surface = 一个业务实例；实例内 update，新实例才 create；action 回包必须复用 `action.surfaceId` 等。
  - `record_surfaces`（after_tool_callback）：解析工具响应里的 A2UI 消息，把 surface 清单写入 session state `a2ui_surfaces`；同时把 `skip_summarization` 改回 `False`。SDK 默认置 `True`（省一次推理），但 ADK 会因此把该工具响应事件判定为 final 并结束本轮，模型就没法在同一轮继续更新“另一个同样展示余额的 surface”（兑换后账户界面不同步的根因）。
  - 多 surface 同轮更新：允许模型把多条消息放进同一次 `send_a2ui_json_to_client`，或同一轮继续调用；前端单界面模式以 action 的 surfaceId 作为“当前界面”，避免被同批的账户界面抢走。
  - `inject_surface_context`（before_model_callback）：每次请求前追加「已存在的 surfaceId / 本轮 action 目标」；该回调在 toolset 的 40k 格式提示词之后执行，因此离模型调用最近。
  - `action_state_delta(action)`：路由层把 `action.surfaceId` 作为 `state_delta` 传给 `runner.run_async`，下一轮注入可直接强调“复用该界面”。
- action 回传：前端 POST `{action: {...}}` → fx_routes 包装为 `{"version":"v0.9","action":{...}}` JSON 文本，作为普通 user message 发给 agent；business instruction 只描述业务，surface 规则由 policy 统一注入。
- 容错：模型偶尔把 A2UI JSON 内联在文本里（`<a2ui-json>...</a2ui-json>`）而不走工具。`stream_chat(a2ui_processor=...)` 开启后，final 文本用 `processor.parse_response(text, wrapped=True)` 拆成 TextPart（→ full 帧）和 A2uiPart（→ a2ui 帧）；`suppress_chunks` 机制保留但默认不启用，避免把标签前的正常文本一起吞掉。

前端（frontend/）：
- `@a2ui/react@0.15 + @a2ui/web_core@0.15`，v0.9 协议（`@a2ui/react/v0_9` 子路径导入）。
- `new MessageProcessor([basicCatalog], action => send({action}))`：第二个参数即 action 回调。
- `processor.processMessages(messages)` 处理 a2ui 帧；`onSurfaceCreated/Deleted` 订阅驱动 `<A2uiSurface surface={surface} />` 渲染。
- 前端展示策略：顶部可切换「单界面（新界面替换旧）」/「多界面（并存）」；多界面支持纵向 / 网格 / 标签页三种排列。
- 「关闭」= 永久移除：前端先本地隐藏（`dismissedIds`），同时回传 `{action:{name:"close_surface", surfaceId}}`；`SURFACE_RULES` 第 9 条要求 agent 用 `deleteSurface` 移除该界面并更新 `a2ui_surfaces`，之后不会再收到它的更新。请求失败时可从「已关闭的界面」恢复。
- 刷新兼容：页面刷新后本地 surface 丢失，若下一轮只有 `updateComponents` / `updateDataModel`，前端会自动补一条 `createSurface` 再处理；`deleteSurface` 指向不存在的 surface 时静默忽略，避免 `A2uiIntegrityError`。
- 注意：`@a2ui/markdown-it` peer 依赖 web_core ^0.14，与 0.15 冲突，未使用（Text 组件按纯文本渲染）。

启动：`cd backend && uv run python app.py`（或 uvicorn app:app）；`cd frontend && npm install && npm run dev` → http://localhost:5173。

## 6. 注意事项 / 潜在改进点

- ~~`chat_sse.py` 中 `streamed = False` 是死代码~~ 已被移除。
- 已通过 `tool_call` / `tool_result` 两种 SSE 帧透传（仅在非 partial 事件上遍历 `event.get_function_calls()` / `event.get_function_responses()`，避免流式参数碎片重复推送）。
- 逐事件 `logger.info(event)` 太吵：已降为 `logger.debug` 摘要（author/partial/tool_calls/final），不再打印完整 event；日志级别统一在 `app.py` 入口配置，默认 INFO，可用 `LOG_LEVEL=DEBUG` 打开；库模块不再各自调用 `logging.basicConfig`。
- `test/agent_runner.py` 演示了如何在前端外用 `event.get_function_calls()` / `event.is_final_response()` 等 API。
- 依赖：`google-adk[db]`, `google-genai`, `litellm`, `fastapi`, `a2ui-adk`, `mcp`（见 `backend/pyproject.toml`）。
