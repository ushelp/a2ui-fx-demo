import type {A2uiMessage} from '@a2ui/web_core/v0_9';

/** SSE 帧，与后端 utils/chat_sse.py 输出的帧类型一一对应。 */
export interface FxFrame {
  type:
    | 'chunk'        // 增量答案文本
    | 'full'         // 聚合完整答案
    | 'thought'      // 增量思考
    | 'thought_full' // 聚合思考
    | 'tool_call'    // 工具调用（name/args）
    | 'tool_result'  // 工具结果（name/resp）
    | 'a2ui'         // A2UI 消息（messages 数组，交给 MessageProcessor）
    | 'error'
    | 'done';
  text?: string;
  message?: string;
  messages?: A2uiMessage[];
  name?: string;
  args?: Record<string, unknown>;
}

export interface FxRequest {
  user_id: string;
  session_id: string;
  message?: string;
  action?: object; // A2UI action 对象（name/surfaceId/context/...）
}

/** POST /api/fx-agent，逐帧读取 SSE 流并回调。 */
export async function streamFxChat(
  req: FxRequest,
  onFrame: (frame: FxFrame) => void,
): Promise<void> {
  const resp = await fetch('/api/fx-agent', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(req),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    const events = buffer.split('\n\n');
    buffer = events.pop() ?? '';
    for (const evt of events) {
      const line = evt.trim();
      if (!line.startsWith('data:')) continue;
      onFrame(JSON.parse(line.slice(5)) as FxFrame);
    }
  }
}
