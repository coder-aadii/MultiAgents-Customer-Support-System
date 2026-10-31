import React from "react";
import { useTranslation } from "../../hooks/useTranslation";

export interface LoadingStateProps {
  message?: string;
}

export function LoadingState({ message }: LoadingStateProps) {
  const { t } = useTranslation();
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-col items-center justify-center p-8 text-gray-500 space-y-2"
    >
      <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-sm">{message || t("common.loading")}</span>
    </div>
  );
}

export interface EmptyStateProps {
  title?: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  const { t } = useTranslation();
  return (
    <div className="text-center py-12 px-4 border border-dashed border-gray-300 rounded-lg">
      <h3 className="text-sm font-medium text-gray-900">{title || t("common.noData")}</h3>
      {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export interface ErrorStateProps {
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({ message, onRetry }: ErrorStateProps) {
  const { t } = useTranslation();
  return (
    <div
      role="alert"
      className="p-4 bg-red-50 border border-red-200 rounded-md text-red-800 space-y-2"
    >
      <p className="text-sm font-medium">{message || t("common.error")}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="text-xs font-semibold text-red-700 underline hover:text-red-900 focus-visible:ring-2 focus-visible:ring-red-500"
        >
          {t("common.retry")}
        </button>
      )}
    </div>
  );
}

