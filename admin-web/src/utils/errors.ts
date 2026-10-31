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

export class AppError extends Error {
  public readonly code: string;
  public readonly requestId?: string;
  public readonly details?: Record<string, unknown>;

  constructor(message: string, code: string = "UNKNOWN_ERROR", requestId?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "AppError";
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

export function isAbortError(error: unknown): boolean {
  if (!error) return false;
  if (axios.isCancel(error)) return true;
  if (error instanceof DOMException && error.name === "AbortError") return true;
  if (typeof error === "object" && "name" in error && error.name === "CanceledError") return true;
  return false;
}

const ERROR_CODE_MAP: Record<string, string> = {
  INVALID_CREDENTIALS: "Invalid email or password",
  UNAUTHORIZED: "Your session has expired. Please sign in again.",
  FORBIDDEN: "You do not have permission to perform this action.",
  NOT_FOUND: "The requested item could not be found.",
  CONVERSATION_CLOSED: "This conversation is closed.",
  VALIDATION_ERROR: "Validation error. Please verify your input.",
  RATE_LIMITED: "Too many requests. Please wait a moment before trying again.",
  INTERNAL_ERROR: "An unexpected error occurred. Please try again later.",
  FILE_OVERSIZED: "File size exceeds maximum allowed limit of 5 MB.",
  UNSUPPORTED_TYPE: "Unsupported file extension. Allowed formats are .txt, .md, and .pdf.",
  PARSER_FAILED: "Failed to parse text from the uploaded file.",
  PDF_ENCRYPTED: "Password-protected or encrypted PDFs are not supported.",
  PDF_MALFORMED: "The uploaded PDF file is malformed or invalid.",
  PDF_SCANNED_OR_NO_TEXT: "Scanned or image-only PDFs with no extractable text are not supported.",
  EMPTY_CONTENT: "Document contains no readable text content.",
  INVALID_DOCUMENT_STATE: "This operation cannot be performed on the document in its current status.",
  TIMEOUT: "Processing timed out. Please retry.",
  PROVIDER_ERROR: "Embedding generation failed. Please try again later.",
};

export function getApiErrorCode(error: unknown): string | null {
  if (axios.isAxiosError(error) && error.response?.data?.error?.code) {
    return String(error.response.data.error.code);
  }
  return null;
}

export function getApiRequestId(error: unknown): string | undefined {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.error?.request_id || (error.response?.headers?.["x-request-id"] as string);
  }
  return undefined;
}

/**
 * Returns a sanitized, safe error message to display in the UI.
 * Never displays raw server exception trace, SQL, or unvetted payloads.
 */
export function toSafeErrorMessage(error: unknown, fallbackMessage = "An unexpected error occurred."): string {
  if (isAbortError(error)) {
    return "Request canceled";
  }

  if (axios.isAxiosError(error)) {
    const data = error.response?.data as ApiErrorResponse | undefined;
    const code = data?.error?.code;

    if (code && ERROR_CODE_MAP[code]) {
      return ERROR_CODE_MAP[code];
    }

    if (error.response?.status === 401) {
      return ERROR_CODE_MAP.UNAUTHORIZED;
    }
    if (error.response?.status === 403) {
      return ERROR_CODE_MAP.FORBIDDEN;
    }
    if (error.response?.status === 404) {
      return ERROR_CODE_MAP.NOT_FOUND;
    }
    if (error.response?.status === 409) {
      return ERROR_CODE_MAP.CONVERSATION_CLOSED;
    }
    if (error.response?.status === 422 || error.response?.status === 400) {
      return ERROR_CODE_MAP.VALIDATION_ERROR;
    }
    if (!error.response && error.request) {
      return "Network error. Please check your internet connection.";
    }
  }

  if (error instanceof AppError) {
    return error.message;
  }

  return fallbackMessage;
}

