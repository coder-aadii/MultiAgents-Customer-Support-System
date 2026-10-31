import React, { useEffect } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { LoadingState } from "./common/UIStates";

interface ProtectedRouteProps {
  children: React.ReactNode;
  adminOnly?: boolean;
  title?: string;
}

export function ProtectedRoute({ children, adminOnly = false, title }: ProtectedRouteProps) {
  const { user, loading } = useAuth();
  const location = useLocation();

  useEffect(() => {
    if (title) {
      document.title = `${title} — MCSS`;
    }
  }, [title]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <LoadingState />
      </div>
    );
  }

  if (!user) {
    // Preserve attempted return path
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  if (adminOnly && user.role !== "admin") {
    // Staff attempting to access admin route is redirected to /inbox
    return <Navigate to="/inbox" replace />;
  }

  return <>{children}</>;
}

