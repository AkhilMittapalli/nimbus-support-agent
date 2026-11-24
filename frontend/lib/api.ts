import { API_URL } from "./config";
import { networkError, toApiError } from "./errors";
import type { CustomerView, GuardrailResult, Mode, Ticket, TurnTrace } from "./protocol";

export interface StoredStep {
  id: string;
  tool: string;
  label: string;
  status: "ok" | "error" | "blocked";
  duration_ms: number;
  summary: string;
  retries: string[];
}

export interface StoredMessage {
  id: number;
  turn_id: string;
  role: "user" | "assistant";
  mode: Mode;
  content: string;
  meta: {
    guardrail?: GuardrailResult;
    tools?: string[];
    steps?: StoredStep[];
    tickets?: string[];
    interrupted?: boolean;
  };
  created_at: string;
}

export interface SessionSnapshot {
  session_id: string;
  status: "active" | "escalated" | "closed";
  customer: CustomerView | null;
  tickets: Ticket[];
  messages: StoredMessage[];
}

export interface DemoCustomer {
  name: string;
  email: string;
  pin: string;
  tier: string;
  contract_status: string;
  scenario: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw networkError();
  }
  if (!res.ok) throw await toApiError(res);
  return (await res.json()) as T;
}

export const api = {
  createSession: () => request<{ session_id: string }>("/api/sessions", { method: "POST" }),
  getSession: (id: string) => request<SessionSnapshot>(`/api/sessions/${encodeURIComponent(id)}`),
  getTraces: (id: string) => request<{ traces: TurnTrace[] }>(`/api/sessions/${encodeURIComponent(id)}/traces`),
  demoCustomers: () => request<{ customers: DemoCustomer[] }>("/api/demo/customers"),
  resetDemo: () => request<{ status: string }>("/api/demo/reset", { method: "POST" }),
  chatUrl: `${API_URL}/api/chat`,
};
