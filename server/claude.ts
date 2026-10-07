/**
 * Thin wrappers around the Claude Agent SDK.
 *
 * Every AI call in Tayyari goes through here. The SDK spawns the Claude Code
 * runtime, which authenticates with whatever Claude Code is logged in with
 * (`claude login` OAuth credentials, or ANTHROPIC_API_KEY if you set one).
 *
 *   runStructured()  – one-shot call that must return JSON matching a zod schema
 *                      (used by the Haiku readers and the Sonnet synthesizer)
 *   streamChat()     – streaming, multi-turn chat with resumable sessions
 *                      (used by the career chat)
 */
import { query, type Options, type SDKMessage } from '@anthropic-ai/claude-agent-sdk';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { z } from 'zod';

export const MODELS = {
  reader: process.env.TAYYARI_READER_MODEL ?? 'claude-haiku-4-5',
  synth: process.env.TAYYARI_SYNTH_MODEL ?? 'claude-sonnet-5-5',
  chat: process.env.TAYYARI_CHAT_MODEL ?? 'claude-sonnet-5-5',
} as const;

const FALLBACK: Record<string, string> = {
  'claude-haiku-4-5': 'haiku',
  'claude-sonnet-5-5': 'sonnet',
};

/** Scratch cwd for agent sessions so they never touch the project tree. */
export const AGENT_CWD = join(tmpdir(), 'tayyari-agent');
mkdirSync(AGENT_CWD, { recursive: true });

/**
 * Environment for the Claude Code subprocess. We inherit everything (PATH,
 * HOME, credentials, proxies) but drop variables that would couple the child
 * to a parent Claude Code session when the server itself is launched from one.
 */
function agentEnv(): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = { ...process.env };
  if (process.env.TAYYARI_AGENT_ENV === 'inherit') return env;
  const sessionCoupled = [
    'CLAUDECODE',
    'CLAUDE_PID',
    'CLAUDE_CODE_ENTRYPOINT',
    'CLAUDE_CODE_SESSION_ID',
    'CLAUDE_CODE_HOST_SESSION_ID',
    'CLAUDE_CODE_CHILD_SESSION',
    'CLAUDE_CODE_MESSAGING_SOCKET',
    'CLAUDE_CODE_MESSAGING_TOKEN',
    'CLAUDE_CODE_SESSION_ATTENDED',
    'CLAUDE_CODE_REPORT_FINDINGS',
    'CLAUDE_CODE_TERMINAL_MCP_TOOLS',
    'CLAUDE_CODE_EMIT_TOOL_USE_SUMMARIES',
    'CLAUDE_CODE_ENABLE_ASK_USER_QUESTION_TOOL',
    'CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING',
    'CLAUDE_AGENT_SDK_VERSION',
  ];
  for (const k of sessionCoupled) delete env[k];
  env.CLAUDE_AGENT_SDK_CLIENT_APP = 'tayyari-poc/0.1.0';
  return env;
}

/** Options shared by every call: isolated from the user's settings, MCP servers and CLAUDE.md. */
function baseOptions(model: string): Options {
  return {
    model,
    fallbackModel: FALLBACK[model],
    cwd: AGENT_CWD,
    env: agentEnv(),
    settingSources: [],
    strictMcpConfig: true,
    mcpServers: {},
    permissionMode: 'dontAsk',
  };
}

export class ClaudeError extends Error {
  constructor(message: string, readonly detail?: unknown) {
    super(message);
    this.name = 'ClaudeError';
  }
}

function describeFailure(msg: Extract<SDKMessage, { type: 'result' }>): string {
  if (msg.subtype === 'success') return msg.result || 'Claude returned an error';
  return `${msg.subtype}${msg.errors?.length ? `: ${msg.errors.join('; ')}` : ''}`;
}

/** Pull the first JSON object out of free text (used only as a fallback). */
function extractJson(text: string): unknown {
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const body = fenced ? fenced[1] : text;
  const start = body.indexOf('{');
  const end = body.lastIndexOf('}');
  if (start === -1 || end <= start) throw new Error('no JSON object in response');
  return JSON.parse(body.slice(start, end + 1));
}

export interface StructuredCall<S extends z.ZodType> {
  model: string;
  system: string;
  prompt: string;
  schema: S;
  /** Built-in tools the agent may use (e.g. ['Read'] to read a PDF, ['WebFetch']). Default: none. */
  tools?: string[];
  /** Extra directories the agent may read (absolute paths). */
  dirs?: string[];
  maxTurns?: number;
  effort?: Options['effort'];
  /** e.g. { type: 'disabled' } for pure extraction calls (Haiku otherwise thinks at length). */
  thinking?: Options['thinking'];
  signal?: AbortSignal;
  /** Called with the running count of generated characters, for progress UIs. */
  onProgress?: (chars: number) => void;
}

export interface StructuredResult<T> {
  data: T;
  costUsd: number;
  durationMs: number;
  model: string;
}

/**
 * One-shot structured call. The SDK enforces the JSON schema (outputFormat),
 * and we validate again with zod so callers get a typed, trusted object.
 */
export async function runStructured<S extends z.ZodType>(call: StructuredCall<S>): Promise<StructuredResult<z.infer<S>>> {
  const jsonSchema = z.toJSONSchema(call.schema, { target: 'draft-7' }) as Record<string, unknown>;
  const abortController = new AbortController();
  call.signal?.addEventListener('abort', () => abortController.abort(), { once: true });

  const tools = call.tools ?? [];
  const options: Options = {
    ...baseOptions(call.model),
    systemPrompt: call.system,
    tools,
    allowedTools: tools,
    additionalDirectories: call.dirs,
    maxTurns: call.maxTurns ?? (tools.length ? 8 : 4),
    effort: call.effort,
    thinking: call.thinking,
    persistSession: false,
    includePartialMessages: !!call.onProgress,
    outputFormat: { type: 'json_schema', schema: jsonSchema },
    abortController,
  };

  let generated = 0;
  let lastText = '';
  for await (const msg of query({ prompt: call.prompt, options })) {
    if (msg.type === 'stream_event' && call.onProgress) {
      const ev = msg.event;
      if (ev.type === 'content_block_delta') {
        const d = ev.delta as { type: string; text?: string; partial_json?: string };
        generated += (d.text ?? d.partial_json ?? '').length;
        call.onProgress(generated);
      }
    } else if (msg.type === 'assistant') {
      for (const block of msg.message.content) {
        if (block.type === 'text') lastText = block.text;
      }
    } else if (msg.type === 'result') {
      if (msg.subtype !== 'success' || msg.is_error) {
        throw new ClaudeError(describeFailure(msg), msg);
      }
      const raw = msg.structured_output ?? (() => {
        try {
          return extractJson(msg.result || lastText);
        } catch {
          return undefined;
        }
      })();
      const parsed = call.schema.safeParse(raw);
      if (!parsed.success) {
        throw new ClaudeError(`structured output failed validation: ${parsed.error.message.slice(0, 400)}`, raw);
      }
      return { data: parsed.data, costUsd: msg.total_cost_usd, durationMs: msg.duration_ms, model: call.model };
    }
  }
  throw new ClaudeError('Claude session ended without a result');
}

export interface ChatCall {
  system: string;
  prompt: string;
  /** Session id from a previous turn of this thread, to continue the conversation. */
  resume?: string;
  model?: string;
  signal?: AbortSignal;
  onText: (delta: string) => void;
  onTool?: (name: string, detail: string) => void;
}

export interface ChatResult {
  sessionId: string;
  text: string;
  costUsd: number;
}

/** Streaming chat turn. Web tools are allowed so Claude can look at public repos and current resources. */
export async function streamChat(call: ChatCall): Promise<ChatResult> {
  const abortController = new AbortController();
  call.signal?.addEventListener('abort', () => abortController.abort(), { once: true });
  const tools = ['WebFetch', 'WebSearch'];
  const options: Options = {
    ...baseOptions(call.model ?? MODELS.chat),
    systemPrompt: call.system,
    tools,
    allowedTools: tools,
    maxTurns: 12,
    effort: 'medium',
    persistSession: true,
    resume: call.resume,
    includePartialMessages: true,
    abortController,
  };

  let sessionId = call.resume ?? '';
  let text = '';
  let sawDelta = false;
  for await (const msg of query({ prompt: call.prompt, options })) {
    if ('session_id' in msg && msg.session_id) sessionId = msg.session_id;
    if (msg.type === 'stream_event') {
      if (msg.parent_tool_use_id) continue;
      const ev = msg.event;
      if (ev.type === 'content_block_delta' && ev.delta.type === 'text_delta') {
        sawDelta = true;
        text += ev.delta.text;
        call.onText(ev.delta.text);
      } else if (ev.type === 'content_block_start' && ev.content_block.type === 'text' && text && !text.endsWith('\n')) {
        // a new text block after a tool call: separate it visually
        text += '\n\n';
        call.onText('\n\n');
      }
    } else if (msg.type === 'assistant' && !msg.parent_tool_use_id) {
      for (const block of msg.message.content) {
        if (block.type === 'tool_use' || block.type === 'server_tool_use') {
          const input = block.input as Record<string, unknown>;
          const detail = String(input.query ?? input.url ?? input.prompt ?? '');
          call.onTool?.(block.name, detail);
        } else if (block.type === 'text' && !sawDelta) {
          text += block.text;
          call.onText(block.text);
        }
      }
    } else if (msg.type === 'result') {
      if (msg.subtype !== 'success' || msg.is_error) throw new ClaudeError(describeFailure(msg), msg);
      return { sessionId: msg.session_id || sessionId, text: text || msg.result, costUsd: msg.total_cost_usd };
    }
  }
  throw new ClaudeError('Claude session ended without a result');
}
