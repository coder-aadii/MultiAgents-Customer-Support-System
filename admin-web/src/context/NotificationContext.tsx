import React, { createContext, useContext, useState, useCallback, useEffect } from "react";

export type NotificationType = "success" | "error" | "warning" | "info";

export interface NotificationItem {
  id: string;
  type: NotificationType;
  title?: string;
  message: string;
  durationMs?: number;
}

interface NotificationContextValue {
  notifications: NotificationItem[];
  notify: (item: Omit<NotificationItem, "id">) => string;
  success: (message: string, title?: string, durationMs?: number) => string;
  error: (message: string, title?: string, durationMs?: number) => string;
  warning: (message: string, title?: string, durationMs?: number) => string;
  info: (message: string, title?: string, durationMs?: number) => string;
  dismiss: (id: string) => void;
  clearAll: () => void;
}

const NotificationContext = createContext<NotificationContextValue | null>(null);

// Global notification event target so non-React code (e.g. axios interceptors) can trigger toasts
type Listener = (notification: NotificationItem) => void;
const globalListeners = new Set<Listener>();

export const notificationService = {
  show: (item: Omit<NotificationItem, "id">): string => {
    const id = "ntf_" + Math.random().toString(36).substring(2, 9) + Date.now().toString(36);
    const notification: NotificationItem = { ...item, id };
    globalListeners.forEach((fn) => fn(notification));
    return id;
  },
  success: (message: string, title?: string, durationMs = 3500) =>
    notificationService.show({ type: "success", message, title, durationMs }),
  error: (message: string, title?: string, durationMs = 4500) =>
    notificationService.show({ type: "error", message, title, durationMs }),
  warning: (message: string, title?: string, durationMs = 4000) =>
    notificationService.show({ type: "warning", message, title, durationMs }),
  info: (message: string, title?: string, durationMs = 3500) =>
    notificationService.show({ type: "info", message, title, durationMs }),
};

export const NotificationProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  }, []);

  const clearAll = useCallback(() => {
    setNotifications([]);
  }, []);

  const notify = useCallback(
    (item: Omit<NotificationItem, "id">): string => {
      const id = "ntf_" + Math.random().toString(36).substring(2, 9) + Date.now().toString(36);
      const newNotification: NotificationItem = {
        ...item,
        id,
        durationMs: item.durationMs ?? 3500,
      };

      setNotifications((prev) => [...prev, newNotification]);

      if (newNotification.durationMs && newNotification.durationMs > 0) {
        setTimeout(() => {
          dismiss(id);
        }, newNotification.durationMs);
      }

      return id;
    },
    [dismiss]
  );

  // Subscribe to external global notification events
  useEffect(() => {
    const handleGlobal = (item: NotificationItem) => {
      setNotifications((prev) => [...prev, item]);
      if (item.durationMs && item.durationMs > 0) {
        setTimeout(() => {
          dismiss(item.id);
        }, item.durationMs);
      }
    };

    globalListeners.add(handleGlobal);
    return () => {
      globalListeners.delete(handleGlobal);
    };
  }, [dismiss]);

  const success = useCallback(
    (message: string, title?: string, durationMs = 3500) =>
      notify({ type: "success", message, title, durationMs }),
    [notify]
  );

  const error = useCallback(
    (message: string, title?: string, durationMs = 4500) =>
      notify({ type: "error", message, title, durationMs }),
    [notify]
  );

  const warning = useCallback(
    (message: string, title?: string, durationMs = 4000) =>
      notify({ type: "warning", message, title, durationMs }),
    [notify]
  );

  const info = useCallback(
    (message: string, title?: string, durationMs = 3500) =>
      notify({ type: "info", message, title, durationMs }),
    [notify]
  );

  return (
    <NotificationContext.Provider
      value={{
        notifications,
        notify,
        success,
        error,
        warning,
        info,
        dismiss,
        clearAll,
      }}
    >
      {children}
      <ToastContainer notifications={notifications} onDismiss={dismiss} />
    </NotificationContext.Provider>
  );
};

export function useNotification(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error("useNotification must be used within a NotificationProvider");
  }
  return context;
}

// Visual Toast Container Component
interface ToastContainerProps {
  notifications: NotificationItem[];
  onDismiss: (id: string) => void;
}

const ToastContainer: React.FC<ToastContainerProps> = ({ notifications, onDismiss }) => {
  if (notifications.length === 0) return null;

  return (
    <aside
      aria-label="Notifications"
      className="fixed top-20 right-6 z-50 flex flex-col space-y-2 pointer-events-none max-w-sm w-full"
    >
      {notifications.map((n) => {
        const isSuccess = n.type === "success";
        const isError = n.type === "error";
        const isWarning = n.type === "warning";

        const icon = isSuccess ? "✓" : isError ? "✕" : isWarning ? "⚠" : "ℹ";

        const typeStyles = isSuccess
          ? "bg-white border-emerald-300 text-gray-900 shadow-lg shadow-emerald-950/5 ring-1 ring-emerald-100"
          : isError
          ? "bg-white border-red-300 text-gray-900 shadow-lg shadow-red-950/5 ring-1 ring-red-100"
          : isWarning
          ? "bg-white border-amber-300 text-gray-900 shadow-lg shadow-amber-950/5 ring-1 ring-amber-100"
          : "bg-white border-gray-300 text-gray-900 shadow-lg ring-1 ring-gray-100";

        const iconColor = isSuccess
          ? "text-emerald-700 bg-emerald-50 border-emerald-300"
          : isError
          ? "text-red-700 bg-red-50 border-red-300"
          : isWarning
          ? "text-amber-700 bg-amber-50 border-amber-300"
          : "text-blue-700 bg-blue-50 border-blue-300";

        return (
          <div
            key={n.id}
            role="status"
            aria-live="polite"
            className={`pointer-events-auto flex items-center p-3 rounded-lg border transition-all transform ease-out duration-200 ${typeStyles}`}
          >
            <div
              className={`flex-shrink-0 w-6 h-6 rounded-full border flex items-center justify-center font-bold text-xs mr-3 ${iconColor}`}
            >
              {icon}
            </div>

            <div className="flex-1 min-w-0 mr-2">
              {n.title && <div className="text-xs font-bold text-gray-900 leading-tight mb-0.5">{n.title}</div>}
              <div className="text-xs font-medium text-gray-800 leading-relaxed break-words">
                {n.message}
              </div>
            </div>

            <button
              type="button"
              onClick={() => onDismiss(n.id)}
              className="flex-shrink-0 text-gray-400 hover:text-gray-700 text-base font-semibold p-1 focus:outline-none transition-colors"
              aria-label="Dismiss notification"
            >
              ×
            </button>
          </div>
        );
      })}
    </aside>
  );
};

