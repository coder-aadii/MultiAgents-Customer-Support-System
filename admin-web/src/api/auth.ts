import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api/v1",
  withCredentials: true,
});

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "staff";
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export const authApi = {
  login: async (email: string, password: string) => {
    await api.post("/auth/login", { email, password });
  },
  refresh: async () => {
    await api.post("/auth/refresh");
  },
  logout: async () => {
    await api.post("/auth/logout");
  },
  me: async (): Promise<User> => {
    const response = await api.get("/auth/me");
    return response.data;
  },
};

export default api;