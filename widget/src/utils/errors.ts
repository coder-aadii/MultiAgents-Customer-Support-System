import axios from "axios";

export interface ApiErrorPayload {
  code: string;
  message: string;
  request_id?: string;
  details?: Record<string, unknown>;
}

export interface ApiErrorResponse {
  error: ApiErrorPayload;
}

export function isAbortError(error: unknown): boolean {
  if (!error) return false;
  if (axios.isCancel(error)) return true;
  if (error instanceof DOMException && error.name === "AbortError") return true;
  if (typeof error === "object" && "name" in error && error.name === "CanceledError") return true;
  return false;
}

const ERROR_CODE_MAP: Record<string, string> = {
  UNAUTHORIZED: "Your session has expired. Please refresh the chat.",
  FORBIDDEN: "Access not permitted.",
  NOT_FOUND: "Conversation not found.",
  CONVERSATION_CLOSED: "This conversation is closed.",
  VALIDATION_ERROR: "Please check your message and try again.",
  RATE_LIMITED: "Too many messages. Please wait a moment.",
  INTERNAL_ERROR: "Something went wrong. Please try again later.",
};

export function toSafeErrorMessage(error: unknown, fallbackMessage = "Unable to send message. Please try again."): string {
  if (isAbortError(error)) {
    return "Request canceled";
  }

  if (axios.isAxiosError(error)) {
    const data = error.response?.data as ApiErrorResponse | undefined;
    const code = data?.error?.code;

    if (code && ERROR_CODE_MAP[code]) {
      return ERROR_CODE_MAP[code];
    }
    if (error.response?.status === 409) {
      return ERROR_CODE_MAP.CONVERSATION_CLOSED;
    }
    if (error.response?.status === 401) {
      return ERROR_CODE_MAP.UNAUTHORIZED;
    }
    if (!error.response && error.request) {
      return "Network error. Please check your connection.";
    }
  }

  return fallbackMessage;
}

