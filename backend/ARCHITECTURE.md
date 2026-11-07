# Backend 架构笔记（Google ADK + FastAPI + SSE）

> 供后续任务快速参考。最后更新：2026-11-07

## 1. 目录结构

```
backend/
├── app.py                     # FastAPI 入口：CORS、/api/health、挂载路由
├── api/
│   ├── agent_routes.py        # POST /api/assistant-agent（SSE 流式聊天）
│   └── user_routes.py         # GET  /api/users（mock 示例）
├── agents/
│   └── assistant_agent.py     # root_agent + Runner 模块级单例
├── utils/
│   ├── adk_utils.py           # AdkUtils.get_adk_litellm_model() 构造 LiteLlm
│   └── chat_sse.py            # 核心：ADK event 流 → SSE
├── tools/
│   └── get_current_time.py    # 示例工具（普通 Python 函数即工具）
├── test/                      # 命令行直连 agent 的调试脚本
└── my_agent_data.db           # SQLite 会话持久化文件
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
| `tool_call`    | `event.get_function_calls()` 非空          | `{"type":"tool_call","id":...,"name":...,"args":{...}}` |
| `tool_result`  | `event.get_function_responses()` 非空      | `{"type":"tool_result","id":...,"name":...,"resp":{...}}` |
| `error`        | 捕获异常                                   | `{"type":"error","message":...}` |
| `done`         | `finally` 必发                             | 流结束标记           |

前端通常：用 `chunk` / `thought` 做打字机，用 `full` / `thought_full` 做最终覆盖，用 `tool_call` / `tool_result` 渲染工具调用过程（可通过 `id` 配对），用 `done` 关闭 Loading。

## 6. 注意事项 / 潜在改进点

- `chat_sse.py` 中 `streamed = False` 是死代码。
- ~~工具调用不推前端~~ 已通过 `tool_call` / `tool_result` 两种 SSE 帧透传（在文本帧处理之前遍历 `event.get_function_calls()` / `event.get_function_responses()`）。
- `logger.info(event)` 逐事件打印，生产环境太吵。
- `test/agent_runner.py` 演示了如何在前端外用 `event.get_function_calls()` / `event.is_final_response()` 等 API。
- 依赖：`google-adk[db]`, `google-genai`, `litellm`, `fastapi`, `a2ui-adk`, `mcp`（见 `backend/pyproject.toml`）。
