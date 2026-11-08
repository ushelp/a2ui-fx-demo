import {
  CSSProperties,
  FormEvent,
  PointerEvent as ReactPointerEvent,
  ReactNode,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {A2uiSurface, ReactCatalogComponent} from '@a2ui/react/v0_9';
import {MessageProcessor, SurfaceModel} from '@a2ui/web_core/v0_9';
import {basicCatalog} from '@a2ui/web_core/v0_9/basic_catalog';
import {streamFxChat} from './api';
import './App.css';

const DEFAULT_USER_ID = 'ray';
const DEFAULT_SESSION_ID = 'fx-demo-session-2';
const STORAGE_USER_ID = 'a2ui-fx-demo.user-id';
const STORAGE_SESSION_ID = 'a2ui-fx-demo.session-id';
const STORAGE_SIDE_WIDTH = 'a2ui-fx-demo.side-width';
const STORAGE_DEBUG_WIDTH = 'a2ui-fx-demo.debug-width';
const STORAGE_POLICY = 'a2ui-fx-demo.display-policy';
const STORAGE_SURFACE_LAYOUT = 'a2ui-fx-demo.surface-layout';

type DisplayPolicy = 'single' | 'multi';
type SurfaceLayout = 'stack' | 'grid' | 'tabs';

const DEFAULT_SIDE_WIDTH = 420;
const DEFAULT_DEBUG_WIDTH = 440;
// 只保留“还能用”的最小宽度，不设上限：UI / 对话 / 调试器都可以拖到接近占满。
const MIN_PANE_WIDTH = 120;
const MIN_DEBUG_WIDTH = 240;
const SPLITTER_WIDTH = 12;

function newSessionId(): string {
  return `fx-${Date.now().toString(36)}`;
}

function loadSetting(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}

function saveSetting(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // localStorage 不可用（隐私模式等）时忽略
  }
}

// 从 v0.9 消息里取出 surfaceId（四种操作都有）
function surfaceIdOf(msg: unknown): string | undefined {
  const m = msg as Record<string, any>;
  return (m.createSurface?.surfaceId ??
    m.updateComponents?.surfaceId ??
    m.updateDataModel?.surfaceId ??
    m.deleteSurface?.surfaceId) as string | undefined;
}

interface ChatMsg {
  role: 'user' | 'agent' | 'tool';
  text: string;
}

// 调试器记录的一条 A2UI 帧
interface A2uiDebugFrame {
  at: string;
  messages: unknown[];
}

interface SendPayload {
  message?: string;
  action?: object;
}

// 轻量 JSON 语法高亮：键 / 字符串 / 数字 / true|false|null
const JSON_TOKEN =
  /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;

function JsonView({value}: {value: unknown}) {
  const code = JSON.stringify(value, null, 2);
  const nodes: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  JSON_TOKEN.lastIndex = 0;
  while ((match = JSON_TOKEN.exec(code)) !== null) {
    if (match.index > last) nodes.push(code.slice(last, match.index));
    const text = match[0];
    let cls = 'json-number';
    if (text.startsWith('"')) cls = text.endsWith(':') ? 'json-key' : 'json-string';
    else if (/^(true|false|null)$/.test(text)) cls = 'json-literal';
    nodes.push(
      <span key={key++} className={cls}>
        {text}
      </span>,
    );
    last = match.index + text.length;
  }
  if (last < code.length) nodes.push(code.slice(last));
  return <pre className="json-code">{nodes}</pre>;
}

export default function App() {
  const [log, setLog] = useState<ChatMsg[]>([]);
  const [streamingText, setStreamingText] = useState('');
  const [streamingThought, setStreamingThought] = useState('');
  const [requesting, setRequesting] = useState(false);
  const [input, setInput] = useState('');
  const [userId, setUserId] = useState(() => loadSetting(STORAGE_USER_ID, DEFAULT_USER_ID));
  const [sessionId, setSessionId] = useState(() =>
    loadSetting(STORAGE_SESSION_ID, DEFAULT_SESSION_ID),
  );
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugFrames, setDebugFrames] = useState<A2uiDebugFrame[]>([]);
  const [sideWidth, setSideWidth] = useState(() => {
    const saved = Number(loadSetting(STORAGE_SIDE_WIDTH, String(DEFAULT_SIDE_WIDTH)));
    if (!Number.isFinite(saved)) return DEFAULT_SIDE_WIDTH;
    return Math.max(MIN_PANE_WIDTH, saved);
  });
  const [debugWidth, setDebugWidth] = useState(() => {
    const saved = Number(loadSetting(STORAGE_DEBUG_WIDTH, String(DEFAULT_DEBUG_WIDTH)));
    if (!Number.isFinite(saved)) return DEFAULT_DEBUG_WIDTH;
    return Math.max(MIN_DEBUG_WIDTH, saved);
  });
  const sideWidthRef = useRef(sideWidth);
  sideWidthRef.current = sideWidth;
  const debugWidthRef = useRef(debugWidth);
  debugWidthRef.current = debugWidth;
  const layoutRef = useRef<HTMLDivElement>(null);

  // 界面展示策略：single = 只保留当前界面；multi = 多个界面并存
  const [displayPolicy, setDisplayPolicy] = useState<DisplayPolicy>(() =>
    loadSetting(STORAGE_POLICY, 'single') === 'multi' ? 'multi' : 'single',
  );
  // 用户主动关闭的 surface：本地立即隐藏，并通知 agent 发送 deleteSurface 永久移除。
  // 若 agent 因故未删除（例如请求失败），仍可在「已关闭的界面」里恢复。
  const [dismissedIds, setDismissedIds] = useState<string[]>([]);
  // 单界面模式下要保留的 surface（最近一次被创建/更新的那个）
  const [activeSurfaceId, setActiveSurfaceId] = useState<string | null>(null);
  const dismissedRef = useRef(dismissedIds);
  dismissedRef.current = dismissedIds;

  // 多界面时的排列方式：纵向 / 网格 / 标签页
  const [surfaceLayout, setSurfaceLayout] = useState<SurfaceLayout>(() => {
    const saved = loadSetting(STORAGE_SURFACE_LAYOUT, 'stack');
    return saved === 'grid' || saved === 'tabs' ? saved : 'stack';
  });

  useEffect(() => {
    saveSetting(STORAGE_POLICY, displayPolicy);
  }, [displayPolicy]);

  useEffect(() => {
    saveSetting(STORAGE_SURFACE_LAYOUT, surfaceLayout);
  }, [surfaceLayout]);

  // 记住最后一次使用的 user/session id，浏览器刷新后继续沿用
  useEffect(() => {
    saveSetting(STORAGE_USER_ID, userId);
  }, [userId]);

  useEffect(() => {
    saveSetting(STORAGE_SESSION_ID, sessionId);
  }, [sessionId]);

  // send 的最新引用，供 A2UI action 回调（组件触发）使用
  const sendRef = useRef<(payload: SendPayload) => Promise<void>>();

  // 对话时间线自动滚动到底部
  const chatRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = chatRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [log, streamingText, streamingThought, requesting]);

  // A2UI 消息处理器：加载 basic catalog；action 回调 → 回传给后端 agent
  const processor = useMemo(
    () =>
      new MessageProcessor<ReactCatalogComponent>([basicCatalog], action => {
        sendRef.current?.({action});
      }),
    [],
  );

  // 订阅 surface 的创建/删除，驱动渲染
  const [surfaces, setSurfaces] = useState<SurfaceModel<ReactCatalogComponent>[]>([]);
  useEffect(() => {
    const sync = () => setSurfaces(Array.from(processor.model.surfacesMap.values()));
    const created = processor.onSurfaceCreated(sync);
    const deleted = processor.onSurfaceDeleted(sync);
    return () => {
      created.unsubscribe();
      deleted.unsubscribe();
    };
  }, [processor]);

  // 主区域变窄（窗口缩放 / 打开调试抽屉）时把已保存的宽度拉回可用范围，
  // 避免拖出来的宽度在小窗口里把布局挤出屏幕。
  useEffect(() => {
    const el = layoutRef.current;
    if (!el) return;
    const clamp = () => {
      const maxSide = Math.max(MIN_PANE_WIDTH, el.clientWidth - SPLITTER_WIDTH - MIN_PANE_WIDTH);
      setSideWidth(w => Math.min(Math.max(w, MIN_PANE_WIDTH), maxSide));
      const maxDebug = Math.max(MIN_DEBUG_WIDTH, window.innerWidth - MIN_PANE_WIDTH);
      setDebugWidth(w => Math.min(Math.max(w, MIN_DEBUG_WIDTH), maxDebug));
    };
    clamp();
    const observer = new ResizeObserver(clamp);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // 单界面模式：只保留 active surface，其余从 renderer 中移除
  useEffect(() => {
    if (displayPolicy !== 'single' || surfaces.length <= 1) return;
    const keep =
      surfaces.find(s => s.id === activeSurfaceId) ?? surfaces[surfaces.length - 1];
    surfaces.filter(s => s.id !== keep.id).forEach(s => processor.model.deleteSurface(s.id));
  }, [displayPolicy, surfaces, activeSurfaceId, processor]);

  // 拖动左/右分栏，调整右侧 UI 的宽度。
  // 上限按主区域的实际宽度计算（只在两边各保留一个最小可用宽度），不再写死像素上限。
  function startResize(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = sideWidthRef.current;
    const layoutWidth = e.currentTarget.parentElement?.clientWidth ?? window.innerWidth;
    const maxWidth = Math.max(MIN_PANE_WIDTH, layoutWidth - SPLITTER_WIDTH - MIN_PANE_WIDTH);
    const onMove = (ev: PointerEvent) => {
      const next = startWidth - (ev.clientX - startX);
      setSideWidth(Math.min(maxWidth, Math.max(MIN_PANE_WIDTH, next)));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.classList.remove('resizing');
      saveSetting(STORAGE_SIDE_WIDTH, String(sideWidthRef.current));
    };
    document.body.classList.add('resizing');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  // 拖动调试抽屉的左边缘，调整它的宽度；同样只保留最小可用宽度
  function startDebugResize(e: ReactPointerEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    const startX = e.clientX;
    const startWidth = debugWidthRef.current;
    const maxWidth = Math.max(MIN_DEBUG_WIDTH, window.innerWidth - MIN_PANE_WIDTH);
    const onMove = (ev: PointerEvent) => {
      const next = startWidth - (ev.clientX - startX);
      setDebugWidth(Math.min(maxWidth, Math.max(MIN_DEBUG_WIDTH, next)));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.classList.remove('resizing');
      saveSetting(STORAGE_DEBUG_WIDTH, String(debugWidthRef.current));
    };
    document.body.classList.add('resizing');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }

  // 清空当前会话的展示状态（新建/切换会话时使用）
  function resetConversation() {
    setLog([]);
    setStreamingText('');
    setStreamingThought('');
    setDebugFrames([]);
    setDismissedIds([]);
    setActiveSurfaceId(null);
    Array.from(processor.model.surfacesMap.keys()).forEach(id =>
      processor.model.deleteSurface(id),
    );
  }

  function handleNewSession() {
    setSessionId(newSessionId());
    resetConversation();
  }

  // 用户关闭 = 永久移除：本地先隐藏，同时回传一个 close_surface action，
  // 由 agent 发送 deleteSurface 并从 session 的 surface 清单中移除。
  function closeSurface(id: string) {
    setDismissedIds(prev => (prev.includes(id) ? prev : [...prev, id]));
    void send({
      action: {
        name: 'close_surface',
        surfaceId: id,
        sourceComponentId: 'surface-close',
        context: {surfaceId: id},
        timestamp: new Date().toISOString(),
      },
    });
  }

  function restoreSurface(id: string) {
    setDismissedIds(prev => prev.filter(x => x !== id));
    if (displayPolicy === 'single') setActiveSurfaceId(id);
  }

  async function send(payload: SendPayload) {
    if (requesting) return;
    setRequesting(true);
    setStreamingText('');
    setStreamingThought('');

    if (payload.message) {
      setLog(prev => [...prev, {role: 'user', text: payload.message!}]);
    } else if (payload.action) {
      const a = payload.action as {name?: string; context?: Record<string, unknown>};
      setLog(prev => [
        ...prev,
        {role: 'user', text: `🖱 提交操作 ${a.name} ${JSON.stringify(a.context ?? {})}`},
      ]);
    }

    let streamed = '';
    let thought = '';
    try {
      await streamFxChat({user_id: userId, session_id: sessionId, ...payload}, frame => {
        switch (frame.type) {
          case 'a2ui':
            // A2UI 消息到达：清掉流式文本（可能是内联 A2UI JSON 的前缀碎片），渲染 UI
            streamed = '';
            setStreamingText('');
            setDebugFrames(prev => [
              ...prev,
              {
                at: new Date().toLocaleTimeString('zh-CN', {hour12: false}),
                messages: frame.messages ?? [],
              },
            ]);
            if (frame.messages?.length) {
              const messages = frame.messages as Array<Record<string, any>>;
              // 本帧可能同时更新多个 surface（例如兑换结果 + 账户余额）。
              // 单界面模式据此保留「当前界面」：action 触发的界面优先，否则取最后触及的。
              const actionSurfaceId = (payload.action as {surfaceId?: string} | undefined)
                ?.surfaceId;
              const touched = messages.map(surfaceIdOf).filter(Boolean) as string[];
              const nextId =
                actionSurfaceId && touched.includes(actionSurfaceId)
                  ? actionSurfaceId
                  : touched[touched.length - 1];
              if (nextId) setActiveSurfaceId(nextId);

              // 后端 session 会保存在服务端，但页面刷新后前端丢失了 surface。
              // 若本轮只有 updateComponents / updateDataModel，先补一条 createSurface，
              // 避免 A2uiIntegrityError: Surface not found；对本地已不存在的
              // deleteSurface 直接忽略。
              const known = new Set(processor.model.surfacesMap.keys());
              const prepared = messages.flatMap(msg => {
                const createId = msg.createSurface?.surfaceId as string | undefined;
                if (createId) {
                  // 用户关闭过该界面，但 agent 又显式创建 → 视为重新打开
                  if (dismissedRef.current.includes(createId)) {
                    setDismissedIds(prev => prev.filter(x => x !== createId));
                  }
                  if (known.has(createId)) processor.model.deleteSurface(createId);
                  known.add(createId);
                  return [msg];
                }
                const updateId = (msg.updateComponents?.surfaceId ??
                  msg.updateDataModel?.surfaceId) as string | undefined;
                if (updateId) {
                  if (!known.has(updateId)) {
                    known.add(updateId);
                    return [
                      {
                        version: 'v0.9',
                        createSurface: {surfaceId: updateId, catalogId: basicCatalog.id},
                      },
                      msg,
                    ];
                  }
                  return [msg];
                }
                const deleteId = msg.deleteSurface?.surfaceId as string | undefined;
                if (deleteId) {
                  if (!known.has(deleteId)) return [];
                  known.delete(deleteId);
                }
                return [msg];
              });
              processor.processMessages(prepared);
            }
            break;
          case 'chunk':
            streamed += frame.text ?? '';
            setStreamingText(streamed);
            break;
          case 'thought':
            // 思考过程增量：持续追加显示
            thought += frame.text ?? '';
            setStreamingThought(thought);
            break;
          case 'thought_full':
            // 思考过程的聚合完整版本
            thought = frame.text ?? '';
            setStreamingThought(thought);
            break;
          case 'full':
            // full 帧是权威最终文本，替代已累积的 chunk
            streamed = '';
            setStreamingText('');
            if (frame.text) setLog(prev => [...prev, {role: 'agent', text: frame.text!}]);
            break;
          case 'tool_call':
            if (frame.name !== 'send_a2ui_json_to_client') {
              setLog(prev => [
                ...prev,
                {role: 'tool', text: `🔧 ${frame.name}(${JSON.stringify(frame.args ?? {})})`},
              ]);
            }
            break;
          case 'error':
            setLog(prev => [...prev, {role: 'agent', text: `⚠️ ${frame.message}`}]);
            break;
        }
      });
      // 流结束仍残留的流式文本（没有 full 帧时）作为最终文本
      if (streamed) setLog(prev => [...prev, {role: 'agent', text: streamed}]);
    } catch (e) {
      setLog(prev => [...prev, {role: 'agent', text: `⚠️ 请求失败：${String(e)}`}]);
    } finally {
      setStreamingText('');
      setRequesting(false);
    }
  }
  sendRef.current = send;

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput('');
    send({message: text});
  }

  const visibleSurfaces = surfaces.filter(s => !dismissedIds.includes(s.id));
  const dismissedSurfaces = surfaces.filter(s => dismissedIds.includes(s.id));
  const shownSurface =
    visibleSurfaces.find(s => s.id === activeSurfaceId) ?? visibleSurfaces[0];

  function renderSurfaceCard(surface: SurfaceModel<ReactCatalogComponent>) {
    return (
      <div key={surface.id} className="surface">
        <div className="surface-head">
          <span className="surface-id">{surface.id}</span>
          <button
            type="button"
            className="surface-close"
            title="关闭这个 surface：通知 agent 永久移除"
            onClick={() => closeSurface(surface.id)}
            disabled={requesting}
          >
            关闭
          </button>
        </div>
        <A2uiSurface surface={surface} />
      </div>
    );
  }

  return (
    <div
      className={`app${debugOpen ? ' debug-open' : ''}`}
      style={{'--debug-width': `${debugWidth}px`} as CSSProperties}
    >
      <header className="topbar">
        <div className="topbar-title">
          <h1>💱 A2UI 汇率助手</h1>
          <p className="hint">
            试试：「美元兑人民币汇率是多少？」→ 在右侧卡片中输入金额 → 点击「兑换」
          </p>
        </div>
        <div className="settings">
          <label className="field">
            <span>User ID</span>
            <input
              value={userId}
              onChange={e => setUserId(e.target.value)}
              disabled={requesting}
              placeholder="user id"
            />
          </label>
          <label className="field">
            <span>Session ID</span>
            <input
              value={sessionId}
              onChange={e => setSessionId(e.target.value)}
              disabled={requesting}
              placeholder="session id"
            />
          </label>
          <label className="field">
            <span>界面策略</span>
            <select
              value={displayPolicy}
              onChange={e => setDisplayPolicy(e.target.value as DisplayPolicy)}
              disabled={requesting}
            >
              <option value="single">单界面（新界面替换旧）</option>
              <option value="multi">多界面（并存可切换）</option>
            </select>
          </label>
          <button
            type="button"
            onClick={handleNewSession}
            disabled={requesting}
            title="生成新的 Session ID 并清空当前对话"
          >
            刷新会话
          </button>
        </div>
      </header>

      <div
        ref={layoutRef}
        className="layout"
        style={{'--side-width': `${sideWidth}px`} as CSSProperties}
      >
        {/* 左侧：文本对话时间线 */}
        <section className="chat-pane">
          <div className="chat" ref={chatRef}>
            {log.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.text}
              </div>
            ))}
            {streamingThought && (
              <details className="msg thought">
                <summary className="thought-label">思考过程</summary>
                {streamingThought}
              </details>
            )}
            {streamingText && <div className="msg agent streaming">{streamingText}</div>}
            {requesting && !streamingText && !streamingThought && (
              <div className="msg agent">思考中…</div>
            )}
          </div>
          <form className="input-row" onSubmit={handleSubmit}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="例如：美元兑人民币汇率是多少？"
              disabled={requesting}
            />
            <button type="submit" disabled={requesting}>
              发送
            </button>
          </form>
        </section>

        <div
          className="splitter"
          role="separator"
          aria-orientation="vertical"
          title="拖动调整右侧宽度"
          onPointerDown={startResize}
        />

        {/* 右侧：当前 UI surface */}
        <aside className="surface-pane">
          <div className="surface-pane-head">
            <span>当前界面</span>
            <div className="pane-actions">
              {visibleSurfaces.length > 0 && (
                <span className="surface-count">{visibleSurfaces.length}</span>
              )}
              <select
                className="surface-layout-select"
                value={surfaceLayout}
                onChange={e => setSurfaceLayout(e.target.value as SurfaceLayout)}
                disabled={displayPolicy === 'single'}
                title={
                  displayPolicy === 'single'
                    ? '切换到「多界面」后可用'
                    : '多界面的排列方式'
                }
              >
                <option value="stack">纵向</option>
                <option value="grid">网格</option>
                <option value="tabs">标签页</option>
              </select>
              <button
                type="button"
                className="debug-toggle"
                onClick={() => setDebugOpen(open => !open)}
              >
                {debugOpen
                  ? '隐藏 JSON'
                  : `A2UI JSON${debugFrames.length ? ` (${debugFrames.length})` : ''}`}
              </button>
            </div>
          </div>
          {visibleSurfaces.length === 0 ? (
            <div className="surface-empty">
              {dismissedSurfaces.length > 0
                ? '界面已关闭，正在通知 agent 移除。'
                : '对话中生成的 UI 会显示在这里。'}
              {dismissedSurfaces.length === 0 && (
                <>
                  <br />
                  agent 更新或删除 surface 时会同步变化。
                </>
              )}
            </div>
          ) : surfaceLayout === 'tabs' ? (
            <>
              <div className="surface-tabs" role="tablist">
                {visibleSurfaces.map(surface => (
                  <button
                    key={surface.id}
                    type="button"
                    role="tab"
                    aria-selected={surface.id === shownSurface?.id}
                    className={`surface-tab${surface.id === shownSurface?.id ? ' active' : ''}`}
                    title={surface.id}
                    onClick={() => setActiveSurfaceId(surface.id)}
                  >
                    {surface.id}
                  </button>
                ))}
              </div>
              {shownSurface && renderSurfaceCard(shownSurface)}
            </>
          ) : surfaceLayout === 'grid' ? (
            <div className="surface-grid">{visibleSurfaces.map(renderSurfaceCard)}</div>
          ) : (
            visibleSurfaces.map(renderSurfaceCard)
          )}
          {dismissedSurfaces.length > 0 && (
            <div className="dismissed-list">
              <div className="dismissed-title">已关闭的界面</div>
              {dismissedSurfaces.map(surface => (
                <div key={surface.id} className="dismissed-item">
                  <span className="surface-id">{surface.id}</span>
                  <button
                    type="button"
                    className="surface-close restore"
                    onClick={() => restoreSurface(surface.id)}
                  >
                    恢复
                  </button>
                </div>
              ))}
            </div>
          )}
        </aside>
      </div>

      {/* 右侧抽屉：A2UI JSON 调试器（压缩主区域，不遮挡界面） */}
      {debugOpen && (
        <section className="debug-drawer">
          <div
            className="debug-resizer"
            title="拖动调整调试器宽度"
            onPointerDown={startDebugResize}
          />
          <div className="debug-head">
            <span>
              A2UI 返回消息{debugFrames.length ? ` · ${debugFrames.length}` : ''}
            </span>
            <div className="debug-actions">
              <button
                type="button"
                onClick={() => setDebugFrames([])}
                disabled={!debugFrames.length}
              >
                清空
              </button>
              <button type="button" onClick={() => setDebugOpen(false)}>
                关闭
              </button>
            </div>
          </div>
          <div className="debug-body">
            {debugFrames.length === 0 ? (
              <div className="debug-empty">
                发送消息后，后端返回的 A2UI JSON 会显示在这里。
              </div>
            ) : (
              debugFrames
                .map((frame, i) => ({frame, index: i + 1}))
                .reverse()
                .map(({frame, index}) => (
                  <details className="debug-frame" key={index}>
                    <summary className="debug-meta">
                      #{index} · {frame.at} · {frame.messages.length} 条消息
                    </summary>
                    <JsonView value={frame.messages} />
                  </details>
                ))
            )}
          </div>
        </section>
      )}
    </div>
  );
}
