const raw = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Backend base URL without a trailing slash. */
export const API_URL = raw.replace(/\/+$/, "");

/** WebSocket base derived from the API URL (http -> ws, https -> wss). */
export const WS_URL = API_URL.replace(/^http/, "ws");
