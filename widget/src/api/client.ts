import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";

export interface WidgetSessionResponse {
  session_token: string;
  visitor_id: string;
  conversation_id: string;
  expires_at: string;
}

export interface WidgetConversation {
  id: string;
  visitor_id: string;
  status: "open" | "staff_active" | "closed";
  assigned_user_id: string | null;
  last_message_at: string;
  created_at: string;
  updated_at: string;
}

export interface WidgetMessage {
  id: string;
  conversation_id: string;
  client_message_id?: string | null;
  sender_type: "visitor" | "staff" | "assistant" | "system";
  content: string;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface VisitorProfileUpdate {
  name?: string | null;
  email?: string | null;
}

export interface VisitorProfile {
  id: string;
  name: string | null;
  email: string | null;
  created_at: string;
  last_seen_at: string;
}

/**
 * Creates an encapsulated visitor-only API client bound to a single visitor session.
 * Cannot access or invoke staff or admin endpoints.
 */
export function createWidgetApiClient(baseUrl?: string) {
  let sessionToken: string | null = null;
  const API_BASE_URL =
    baseUrl || (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE_URL) || "/api/v1";

  const client: AxiosInstance = axios.create({
    baseURL: API_BASE_URL,
  });

  client.interceptors.request.use((config: InternalAxiosRequestConfig) => {
    // Only allow calling /widget/* routes
    const url = config.url || "";
    if (!url.startsWith("/widget")) {
      throw new Error(`Widget client forbidden from accessing non-widget route: ${url}`);
    }

    if (sessionToken && !config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${sessionToken}`;
    }
    return config;
  });

  return {
    setSessionToken: (token: string | null) => {
      sessionToken = token;
    },
    getSessionToken: () => sessionToken,
    clearSession: () => {
      sessionToken = null;
    },
    createSession: async (
      payload?: { name?: string; email?: string; customer_token?: string },
      signal?: AbortSignal
    ): Promise<WidgetSessionResponse> => {
      const res = await client.post<WidgetSessionResponse>("/widget/sessions", payload || {}, {
        signal,
      });
      sessionToken = res.data.session_token;
      return res.data;
    },
    getConversation: async (signal?: AbortSignal): Promise<WidgetConversation> => {
      const res = await client.get<WidgetConversation>("/widget/conversation", { signal });
      return res.data;
    },
    listMessages: async (
      params?: { cursor?: string | null; limit?: number },
      signal?: AbortSignal
    ): Promise<PaginatedResponse<WidgetMessage>> => {
      const res = await client.get<PaginatedResponse<WidgetMessage>>("/widget/messages", {
        params,
        signal,
      });
      return res.data;
    },
    sendMessage: async (data: {
      content: string;
      client_message_id: string;
    }): Promise<WidgetMessage> => {
      const res = await client.post<WidgetMessage>("/widget/messages", data);
      return res.data;
    },
    updateProfile: async (data: VisitorProfileUpdate): Promise<VisitorProfile> => {
      const res = await client.patch<VisitorProfile>("/widget/profile", data);
      return res.data;
    },
  };
}
