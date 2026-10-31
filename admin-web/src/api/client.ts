import axios, { type AxiosError, type InternalAxiosRequestConfig } from "axios";

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "staff";
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface PaginatedResponse<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface VisitorInfo {
  id: string;
  name: string | null;
  email: string | null;
  created_at: string;
  last_seen_at: string;
}

export interface Conversation {
  id: string;
  visitor_id: string;
  status: "open" | "waiting_for_staff" | "staff_active" | "closed";
  assigned_user_id: string | null;
  title: string | null;
  last_message_at: string;
  created_at: string;
  updated_at: string;
  assignee?: User | null;
  visitor?: VisitorInfo | null;
}

export interface Message {
  id: string;
  conversation_id: string;
  client_message_id?: string | null;
  sender_type: "visitor" | "staff" | "assistant" | "system";
  sender_user_id?: string | null;
  content: string;
  metadata?: {
    cited_chunk_ids?: string[];
    confidence?: number;
    handoff_reason?: string;
    model?: string;
  };
  created_at: string;
}

export interface Handoff {
  id: string;
  conversation_id: string;
  reason: string;
  summary?: string | null;
  status: "queued" | "claimed" | "resolved" | "dismissed";
  claimed_by_user_id?: string | null;
  created_at: string;
}

// In-memory staff access token storage
let currentAccessToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;
let authExpiredListener: (() => void) | null = null;

export const tokenManager = {
  getAccessToken: () => currentAccessToken,
  setAccessToken: (token: string | null) => {
    currentAccessToken = token;
  },
  clear: () => {
    currentAccessToken = null;
    refreshPromise = null;
  },
  setOnAuthExpired: (listener: (() => void) | null) => {
    authExpiredListener = listener;
  },
  triggerAuthExpired: () => {
    currentAccessToken = null;
    if (authExpiredListener) {
      authExpiredListener();
    }
  },
};

const API_BASE_URL =
  (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_BASE_URL) || "/api/v1";

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true, // required for httpOnly refresh token cookie
});

// Attach Authorization header if in-memory token exists
apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenManager.getAccessToken();
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Response interceptor with Phase 1 refresh rotation contract
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as InternalAxiosRequestConfig & { _retry?: boolean };

    if (!error.response || error.response.status !== 401 || !originalRequest) {
      return Promise.reject(error);
    }

    // Do not attempt refresh if the failed call was itself login or refresh
    const requestUrl = originalRequest.url || "";
    if (requestUrl.includes("/auth/login") || requestUrl.includes("/auth/refresh")) {
      tokenManager.triggerAuthExpired();
      return Promise.reject(error);
    }

    if (originalRequest._retry) {
      tokenManager.triggerAuthExpired();
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    // Concurrently deduplicate refresh requests
    if (!refreshPromise) {
      refreshPromise = (async () => {
        try {
          const res = await axios.post<TokenResponse>(
            `${API_BASE_URL}/auth/refresh`,
            {},
            { withCredentials: true }
          );
          const newToken = res.data.access_token;
          tokenManager.setAccessToken(newToken);
          return newToken;
        } catch (refreshErr) {
          tokenManager.triggerAuthExpired();
          return null;
        } finally {
          refreshPromise = null;
        }
      })();
    }

    const newToken = await refreshPromise;
    if (!newToken) {
      return Promise.reject(error);
    }

    originalRequest.headers.Authorization = `Bearer ${newToken}`;
    return apiClient(originalRequest);
  }
);

export const authApi = {
  login: async (email: string, password: string): Promise<TokenResponse> => {
    const res = await apiClient.post<TokenResponse>("/auth/login", { email, password });
    tokenManager.setAccessToken(res.data.access_token);
    return res.data;
  },
  refresh: async (): Promise<TokenResponse> => {
    const res = await apiClient.post<TokenResponse>("/auth/refresh");
    tokenManager.setAccessToken(res.data.access_token);
    return res.data;
  },
  logout: async (): Promise<void> => {
    try {
      await apiClient.post("/auth/logout");
    } finally {
      tokenManager.clear();
    }
  },
  me: async (): Promise<User> => {
    const res = await apiClient.get<User>("/auth/me");
    return res.data;
  },
};

export const conversationsApi = {
  list: async (
    params?: { status?: string; cursor?: string | null; limit?: number },
    signal?: AbortSignal
  ): Promise<PaginatedResponse<Conversation>> => {
    const res = await apiClient.get<PaginatedResponse<Conversation>>("/conversations", {
      params,
      signal,
    });
    return res.data;
  },
  get: async (id: string, signal?: AbortSignal): Promise<Conversation> => {
    const res = await apiClient.get<Conversation>(`/conversations/${id}`, { signal });
    return res.data;
  },
  claim: async (id: string): Promise<Conversation> => {
    const res = await apiClient.post<Conversation>(`/conversations/${id}/claim`);
    return res.data;
  },
  close: async (id: string): Promise<Conversation> => {
    const res = await apiClient.post<Conversation>(`/conversations/${id}/close`);
    return res.data;
  },
};

export const messagesApi = {
  list: async (
    conversationId: string,
    params?: { cursor?: string | null; limit?: number },
    signal?: AbortSignal
  ): Promise<PaginatedResponse<Message>> => {
    const res = await apiClient.get<PaginatedResponse<Message>>(
      `/conversations/${conversationId}/messages`,
      { params, signal }
    );
    return res.data;
  },
  send: async (
    conversationId: string,
    data: { content: string; client_message_id: string }
  ): Promise<Message> => {
    const res = await apiClient.post<Message>(
      `/conversations/${conversationId}/messages`,
      data
    );
    return res.data;
  },
};

export const handoffsApi = {
  list: async (
    params?: { status?: string; cursor?: string | null; limit?: number },
    signal?: AbortSignal
  ): Promise<PaginatedResponse<Handoff>> => {
    const res = await apiClient.get<PaginatedResponse<Handoff>>("/handoffs", {
      params,
      signal,
    });
    return res.data;
  },
  claim: async (id: string): Promise<Handoff> => {
    const res = await apiClient.post<Handoff>(`/handoffs/${id}/claim`);
    return res.data;
  },
};
