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
  priority?: "low" | "normal" | "high" | "urgent";
  sla_breached?: boolean;
  channel_type?: string;
  assigned_team_id?: string | null;
  assigned_agent_id?: string | null;
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

export type DocumentStatus = "draft" | "published" | "archived";
export type IngestionStatus = "pending" | "processing" | "ready" | "failed";
export type SourceType = "text" | "markdown" | "pdf";

export interface KnowledgeChunk {
  id: string;
  ordinal: number;
  content: string;
  token_estimate: number;
  created_at: string;
}

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_type: SourceType;
  source_filename?: string | null;
  content_hash?: string | null;
  status: DocumentStatus;
  ingestion_status: IngestionStatus;
  ingestion_error_code?: string | null;
  chunk_count: number;
  created_by_user_id: string;
  published_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocument {
  content: string;
  chunks: KnowledgeChunk[];
}

export interface RetrievalResultItem {
  chunk_id: string;
  document_id: string;
  document_title: string;
  rank: number;
  score: number;
  content: string;
  token_estimate: number;
  start_char: number;
  end_char: number;
}

export interface RetrievalDebugResponse {
  query: string;
  results: RetrievalResultItem[];
  total_found: number;
  threshold: number;
  total_chunks_scanned: number;
  excluded_unpublished_matches: number;
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

export const knowledgeApi = {
  list: async (
    params?: {
      status?: string;
      ingestion_status?: string;
      cursor?: string | null;
      limit?: number;
    },
    signal?: AbortSignal
  ): Promise<PaginatedResponse<KnowledgeDocument>> => {
    const res = await apiClient.get<PaginatedResponse<KnowledgeDocument>>(
      "/knowledge/documents",
      { params, signal }
    );
    return res.data;
  },
  get: async (id: string, signal?: AbortSignal): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.get<KnowledgeDocumentDetail>(`/knowledge/documents/${id}`, {
      signal,
    });
    return res.data;
  },
  create: async (data: {
    title: string;
    source_type: SourceType;
    content: string;
  }): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.post<KnowledgeDocumentDetail>("/knowledge/documents", data);
    return res.data;
  },
  upload: async (
    formData: FormData,
    onUploadProgress?: (progressEvent: any) => void
  ): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.post<KnowledgeDocumentDetail>(
      "/knowledge/documents/upload",
      formData,
      {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress,
      }
    );
    return res.data;
  },
  update: async (
    id: string,
    data: { title?: string; content?: string }
  ): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.patch<KnowledgeDocumentDetail>(
      `/knowledge/documents/${id}`,
      data
    );
    return res.data;
  },
  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/knowledge/documents/${id}`);
  },
  publish: async (id: string): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.post<KnowledgeDocumentDetail>(
      `/knowledge/documents/${id}/publish`
    );
    return res.data;
  },
  archive: async (id: string): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.post<KnowledgeDocumentDetail>(
      `/knowledge/documents/${id}/archive`
    );
    return res.data;
  },
  retry: async (id: string): Promise<KnowledgeDocumentDetail> => {
    const res = await apiClient.post<KnowledgeDocumentDetail>(
      `/knowledge/documents/${id}/retry`
    );
    return res.data;
  },
  debugRetrieve: async (data: {
    query: string;
    limit?: number;
    min_score?: number;
  }): Promise<RetrievalDebugResponse> => {
    const res = await apiClient.post<RetrievalDebugResponse>("/knowledge/retrieve/debug", data);
    return res.data;
  },
};

export interface BusinessActionItem {
  id: string;
  conversation_id: string;
  customer_id: string;
  action_name: string;
  risk_level: "low" | "medium" | "high" | "critical";
  status: string;
  idempotency_key: string;
  payload_json: Record<string, any>;
  result_json: Record<string, any> | null;
  summary_message: string;
  required_confirmation_phrase: string;
  expires_at: string;
  confirmed_at: string | null;
  reviewed_by_user_id: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  execution_attempts: number;
  created_at: string;
  updated_at: string;
}

export const actionsApi = {
  listPending: async (risk_level?: string): Promise<BusinessActionItem[]> => {
    const res = await apiClient.get<BusinessActionItem[]>("/admin/actions/pending", {
      params: risk_level ? { risk_level } : undefined,
    });
    return res.data;
  },
  approve: async (id: string, notes?: string): Promise<any> => {
    const res = await apiClient.post(`/admin/actions/${id}/approve`, { notes });
    return res.data;
  },
  reject: async (id: string, reason: string): Promise<BusinessActionItem> => {
    const res = await apiClient.post<BusinessActionItem>(`/admin/actions/${id}/reject`, { reason });
    return res.data;
  },
};

export interface ConversationNote {
  id: string;
  conversation_id: string;
  author_user_id: string;
  note_text: string;
  is_whisper: boolean;
  created_at: string;
}

export interface AgentRosterItem {
  id: string;
  display_name: string;
  email: string;
  role: "admin" | "staff";
  presence: "online" | "busy" | "away" | "offline";
  active_chats: number;
  teams: string[];
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  has_more: boolean;
}

export const operationsApi = {
  listAgents: async (params?: { limit?: number; offset?: number }): Promise<AgentRosterItem[] | PaginatedResponse<AgentRosterItem>> => {
    const res = await apiClient.get<AgentRosterItem[] | PaginatedResponse<AgentRosterItem>>("/admin/operations/agents", {
      params,
    });
    return res.data;
  },
  listNotes: async (conversationId: string): Promise<ConversationNote[]> => {
    const res = await apiClient.get<ConversationNote[]>(`/conversations/${conversationId}/notes`);
    return res.data;
  },
  addNote: async (
    conversationId: string,
    noteText: string,
    isWhisper = false
  ): Promise<ConversationNote> => {
    const res = await apiClient.post<ConversationNote>(`/conversations/${conversationId}/notes`, {
      note_text: noteText,
      is_whisper: isWhisper,
    });
    return res.data;
  },
  getRealtimeAnalytics: async (): Promise<any> => {
    const res = await apiClient.get("/admin/analytics/realtime");
    return res.data;
  },
  getMetrics: async (): Promise<any> => {
    const res = await apiClient.get("/admin/analytics/metrics");
    return res.data;
  },
  listTeams: async (): Promise<any[]> => {
    const res = await apiClient.get("/admin/operations/teams");
    return res.data;
  },
  reassignConversation: async (
    conversationId: string,
    payload: { team_id?: string; agent_id?: string }
  ): Promise<any> => {
    const res = await apiClient.post(
      `/admin/operations/conversations/${conversationId}/assign`,
      payload
    );
    return res.data;
  },
};
