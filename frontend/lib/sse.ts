import { networkError, toApiError } from "./errors";
import { parseAgentEvent, type AgentEvent } from "./protocol";

/**
 * POST a JSON body and consume a text/event-stream response.
 * EventSource only supports GET, so this parses the SSE wire format from a fetch stream.
 * Throws ApiError for non-2xx responses (e.g. 429 rate limit) and network failures;
 * rethrows AbortError when the caller aborts.
 */
export async function postSSE(
  url: string,
  body: unknown,
  onEvent: (event: AgentEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (signal.aborted) throw err;
    throw networkError();
  }
  if (!res.ok) throw await toApiError(res);
  if (!res.body) throw networkError();

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      // Events are separated by a blank line; normalise CRLF first.
      buffer = buffer.replace(/\r\n/g, "\n");
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const data = raw
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!data) continue; // comment / keep-alive
        try {
          const event = parseAgentEvent(JSON.parse(data));
          if (event) onEvent(event);
        } catch {
          // ignore malformed frame
        }
      }
    }
  } catch (err) {
    if (signal.aborted) throw err;
    throw networkError();
  } finally {
    reader.releaseLock();
  }
}
