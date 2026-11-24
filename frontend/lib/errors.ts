export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ErrorDetail {
  code?: unknown;
  message?: unknown;
  retry_after?: unknown;
}

/** Turn a non-2xx fetch Response into a typed ApiError (handles FastAPI's {detail: ...} shape). */
export async function toApiError(res: Response): Promise<ApiError> {
  let detail: ErrorDetail = {};
  try {
    const body: unknown = await res.json();
    if (typeof body === "object" && body !== null) {
      const d = (body as { detail?: unknown }).detail ?? body;
      if (typeof d === "object" && d !== null) detail = d as ErrorDetail;
      else if (typeof d === "string") detail = { message: d };
    }
  } catch {
    // non-JSON error body
  }
  const code = typeof detail.code === "string" ? detail.code : res.status === 429 ? "rate_limited" : `http_${res.status}`;
  const message =
    typeof detail.message === "string"
      ? detail.message
      : res.status === 429
        ? "You're sending messages too quickly. Please wait a moment."
        : res.status >= 500
          ? "The support service is temporarily unavailable. Please try again."
          : `Request failed (${res.status}).`;
  const retryAfter = typeof detail.retry_after === "number" ? detail.retry_after : undefined;
  return new ApiError(res.status, code, message, res.status === 429 || res.status >= 500, retryAfter);
}

export function networkError(): ApiError {
  return new ApiError(0, "network_error", "Can't reach the support service. Check your connection and retry.", true);
}
