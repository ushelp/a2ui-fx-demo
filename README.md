# A2UI 汇率助手 Demo（Google ADK + FastAPI + React）

一个端到端的 [A2UI](https://a2ui.org/)（Agent to User Interface）示例：用户发一句「美元兑人民币汇率是多少？」，
Agent 流式返回一张可交互的汇率卡片（含账户各币种余额）；用户在卡片里输入金额、点击「兑换」，
前端把 A2UI action 回传给 Agent，Agent 执行兑换（扣减 / 增加 mock 账户余额）并更新同一张卡片为兑换结果。

- 后端：Google ADK（`a2ui-adk` 扩展，**不使用 A2A**）+ FastAPI + SSE 流式输出
- 前端：Vite + React 18 + TypeScript + 官方渲染器 `@a2ui/react`（v0.9 协议）
- 汇率与账户余额全部为 mock，无外部依赖

## 目录结构

```
backend/
├── app.py                     # FastAPI 入口：日志、CORS、/api/health、路由挂载
├── api/
│   ├── agent_routes.py        # POST /api/assistant-agent（普通 SSE 聊天）
│   ├── fx_routes.py           # POST /api/fx-agent（A2UI 汇率助手，SSE + action）
│   └── user_routes.py         # GET  /api/users（示例）
├── agents/
│   ├── a2ui_policy.py         # 通用 surface 规则 + session 状态注入回调
│   ├── assistant_agent.py     # 基础 ADK agent
│   └── fx_agent.py            # A2UI 汇率 agent（a2ui-adk toolset，UI 由模型自由组合）
├── tools/
│   ├── get_current_time.py
│   └── fx_tools.py            # mock：get_exchange_rate / convert_currency / get_account_balances / exchange_in_account
├── utils/
│   ├── adk_utils.py           # LiteLlm 模型构造（默认本地 Ollama，可换 provider）
│   └── chat_sse.py            # ADK event 流 → SSE 帧（含 a2ui 帧）
└── ARCHITECTURE.md            # 代码结构 / SSE 协议 / 实现细节（建议先读）

frontend/
├── src/api.ts                 # fetch + ReadableStream 读取 SSE
├── src/App.tsx                # MessageProcessor + <A2uiSurface> + action 回传
└── vite.config.ts             # /api → 127.0.0.1:8000 代理
```

## 环境要求

- Python >= 3.12（后端已带 `.venv`）
- Node.js >= 18（前端）
- 可用的 LLM：默认走本地 Ollama；用 `.env` 覆盖即可切到任意 LiteLLM 支持的 provider

## 启动

### 1. 后端（端口 8000）

```bash
cd backend
uv run python app.py
# 或
uv run uvicorn app:app --reload
```

健康检查：<http://127.0.0.1:8000/api/health>

### 2. 前端（端口 5173）

```bash
cd frontend
npm install        # 首次
npm run dev
```

打开 <http://localhost:5173>

### 环境变量（`backend/.env`）

| 变量 | 说明 | 默认 |
|---|---|---|
| `LLM_MODEL` | LiteLLM 模型名，如 `ollama_chat/qwen3.8:27b-mlx`、`dashscope/qwen-max` | `ollama_chat/qwen3.8:27b-mlx` |
| `LLM_API_BASE` | provider 地址 | `http://localhost:11434` |
| `LLM_API_KEY` | provider key（云端模型需要） | 空 |
| `LOG_LEVEL` | 日志级别，`DEBUG` 可看逐事件摘要 | `INFO` |
| `HOST` / `PORT` | 后端监听地址 | `127.0.0.1` / `8000` |
| `CORS_ORIGINS` | 允许的前端 Origin（逗号分隔） | `http://localhost:5173,http://127.0.0.1:5173` |

## 测试流程

1. 输入：`美元兑人民币汇率是多少？`
   → 前端出现卡片：`1 USD = 7.10 CNY` + 账户各币种余额 + 金额输入框 + 「兑换」按钮
2. 在卡片输入 `200`，点击「兑换」
   → 同一张卡片更新为兑换结果：`200 USD = 1420.00 CNY`，并显示扣减 / 增加后的账户余额
3. 输入：`我的账户余额还有多少？`
   → Agent 返回账户余额界面（mock 账户在进程内共享，后端重启后重置）

也可直接用 curl 验证 SSE（把消息换成自己的）：

```bash
curl -N -X POST http://127.0.0.1:8000/api/fx-agent \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"ray","session_id":"t1","message":"美元兑人民币汇率是多少？"}'
```

## 接口与 SSE 协议

### `POST /api/fx-agent`

请求体：

```jsonc
// 普通文本消息
{"user_id": "ray", "session_id": "fx-1", "message": "美元兑人民币汇率是多少？"}
// A2UI action 回传（前端组件触发）
{"user_id": "ray", "session_id": "fx-1", "action": {"name": "exchange_in_account", "surfaceId": "fx-usd-cny", "context": {"amount": "200", "base_currency": "USD", "quote_currency": "CNY"}}}
```

响应：`text/event-stream`，每帧 `data: {json}\n\n`。

| type | 含义 |
|---|---|
| `chunk` / `full` | 增量答案 / 本轮完整答案 |
| `thought` / `thought_full` | 增量思考 / 完整思考 |
| `tool_call` / `tool_result` | 工具调用与结果（`get_exchange_rate`、`convert_currency` 等） |
| `a2ui` | `{"type":"a2ui","messages":[...]}`，直接交给前端 MessageProcessor 渲染 |
| `error` / `done` | 错误 / 流结束 |

> `surfaceId` 由 agent 生成（示例中为 `fx-usd-cny`），同一界面的后续更新（`updateComponents` / `updateDataModel`）沿用该 id；不同界面可以同时存在，前端按 id 分别渲染。展示策略可在页面顶部切换「单界面（新界面替换旧）」或「多界面（并存）」；多界面模式下还可选择「纵向 / 网格 / 标签页」排列（右侧「当前界面」标题栏）；「关闭」会回传 `close_surface` action，由 agent 发送 `deleteSurface` 把它从 session 的 surface 清单中永久移除，之后不会再收到该界面的更新；需要时重新提问即可再次生成。若 agent 因故没有删除（例如请求失败），仍可在「已关闭的界面」里恢复。

前端收到 `a2ui` 帧即 `processor.processMessages(messages)`；组件 action 通过
`MessageProcessor` 的回调 POST 回 `/api/fx-agent`，形成闭环。

## 实现要点

- `A2uiRequestProcessor(catalogs=[BasicCatalog("v0.9")], format_factory=DirectJsonFormatFactory())`
  - 不传 `examples`：UI 由模型每轮自由组合，避免 few-shot 变成固定模板；格式正确性由
    catalog schema 与处理器校验保证，校验失败会回给模型重试。
- `SendA2uiToClientToolset` 暴露 `send_a2ui_json_to_client` 工具；校验后的消息在工具响应的
  `validated_a2ui_json` 字段，由 `utils/chat_sse.py` 拦截转成 `a2ui` 帧。
- 容错：模型偶尔把 A2UI 内联成 `<a2ui-json>...</a2ui-json>` 文本，SSE 层会用
  `processor.parse_response` 解析并按帧拆分，不会把裸 JSON 漏给用户。
- A2UI 格式提示词由 `SendA2uiToClientToolset` 在每次请求前自动追加（无需再叠加 `A2uiInstruction`）。
- 通用 action 契约与 surface 规则集中在 `agents/a2ui_policy.py`：`action.name` 对齐工具名、
  `context` 的 key 对齐工具参数名，业务 instruction 只描述业务本身。
- 前端不再每轮删除 surface：update-only 帧复用已有 surface，刷新后本地缺失时自动补 `createSurface`。

更多内部细节（含 SSE 帧实现、a2ui-adk 集成、踩坑记录）见
[backend/ARCHITECTURE.md](backend/ARCHITECTURE.md)。

## 已知限制

- 汇率与兑换结果均为 mock 固定值（USD/CNY/EUR/JPY/GBP/HKD）。
- 未安装 `@a2ui/markdown-it`（其 peer 依赖 `@a2ui/web_core@^0.14` 与当前 0.15 冲突），
  文本按纯文本渲染。
- `a2ui-adk` 的 `A2uiInstruction` / `SendA2uiToClientToolset` 标注为 EXPERIMENTAL，启动时会出现
  UserWarning，属正常现象。
- 未实现 A2A / 多 agent 编排，只使用 `a2ui-adk` 本地集成。
