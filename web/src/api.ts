import type {
  ActionProposal,
  BridgeHealth,
  BridgeReport,
  DecisionValue,
  RunbookEventName,
  RunbookEventPayload,
  SlackMessage
} from "./contracts";
import { redactSecrets, redactSecretsDeep } from "./redaction";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers
      }
    });
  } catch (error) {
    throw new ApiError(
      error instanceof Error ? redactSecrets(error.message) : "Network request failed"
    );
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`.trim();
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // The status is still useful when the body is not JSON.
    }
    throw new ApiError(redactSecrets(detail), response.status);
  }
  return redactSecretsDeep((await response.json()) as T);
}

export function getBridgeHealth(signal?: AbortSignal): Promise<BridgeHealth> {
  return requestJson<BridgeHealth>("/bridge/health", { signal });
}

export async function getSlackMessages(signal?: AbortSignal): Promise<SlackMessage[]> {
  const body = await requestJson<unknown>("/bridge/slack/messages", { signal });
  const raw = Array.isArray(body)
    ? body
    : body && typeof body === "object" && Array.isArray((body as { messages?: unknown[] }).messages)
      ? (body as { messages: unknown[] }).messages
      : [];

  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item, index) => ({
      ...item,
      id: String(item.id ?? item.client_msg_id ?? item.ts ?? `slack-${index}`),
      text: String(item.text ?? ""),
      user: item.user == null ? undefined : String(item.user),
      user_name: item.user_name == null ? undefined : String(item.user_name),
      channel: item.channel == null ? undefined : String(item.channel),
      name: item.name == null ? undefined : String(item.name),
      channel_name:
        item.channel_name == null
          ? item.name == null
            ? undefined
            : String(item.name)
          : String(item.channel_name),
      timestamp: item.timestamp == null ? undefined : String(item.timestamp),
      ts: item.ts == null ? undefined : String(item.ts),
      permalink: item.permalink == null ? undefined : String(item.permalink)
    }))
    .filter((message) => message.text.trim().length > 0);
}

export interface ComplaintRequest {
  text: string;
  service?: string;
  channel?: string;
  external_id?: string;
  graph_enabled?: boolean;
  severity?: "critical" | "warning" | "info";
}

export interface ComplaintAccepted {
  alert_id?: string;
  status?: string;
  [key: string]: unknown;
}

export function postComplaint(payload: ComplaintRequest): Promise<ComplaintAccepted> {
  return requestJson<ComplaintAccepted>("/bridge/complaint", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export interface DecisionRequest {
  proposal_id: string;
  decision: DecisionValue;
  reviewer_note?: string;
  modified_action?: ActionProposal;
  typed_confirmation?: string;
}

export function postDecision(payload: DecisionRequest) {
  return requestJson("/decisions", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getBridgeReport(alertId: string): Promise<BridgeReport> {
  return requestJson<BridgeReport>(`/bridge/report/${encodeURIComponent(alertId)}`);
}

const EVENT_NAMES: RunbookEventName[] = [
  "alert",
  "hypotheses",
  "proposal",
  "decision",
  "action",
  "outcome",
  "resolution",
  "bridge_report"
];

export function subscribeEvents(
  onEvent: (name: RunbookEventName, payload: RunbookEventPayload) => void,
  onConnection: (connected: boolean) => void
): () => void {
  const source = new EventSource(`${API_BASE}/events`);
  source.onopen = () => onConnection(true);
  source.onerror = () => onConnection(false);

  const listeners = EVENT_NAMES.map((name) => {
    const listener = (event: Event) => {
      try {
        const payload = redactSecretsDeep(
          JSON.parse((event as MessageEvent<string>).data) as RunbookEventPayload
        );
        onEvent(name, payload);
      } catch {
        // Ignore malformed frames; later valid named events remain usable.
      }
    };
    source.addEventListener(name, listener);
    return { name, listener };
  });

  return () => {
    listeners.forEach(({ name, listener }) => source.removeEventListener(name, listener));
    source.close();
  };
}
