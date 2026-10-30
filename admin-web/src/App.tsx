import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "./hooks/useAuth";
import Login from "./pages/Login";
import Inbox from "./pages/Inbox";
import Knowledge from "./pages/Knowledge";
import Settings from "./pages/Settings";
import Audit from "./pages/Audit";
import Layout from "./components/Layout";

function PrivateRoute({ children, adminOnly = false }: { children: React.ReactNode; adminOnly?: boolean }) {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (adminOnly && user.role !== "admin") {
    return <Navigate to="/inbox" replace />;
  }

  return <>{children}</>;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <PrivateRoute>
            <Layout />
          </PrivateRoute>
        }
      >
        <Route path="/inbox" element={<Inbox />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/knowledge/new" element={<Knowledge />} />
        <Route path="/knowledge/:id" element={<Knowledge />} />
        <Route
          element={
            <PrivateRoute adminOnly>
              <Settings />
            </PrivateRoute>
          }
        >
          <Route path="/settings/widget" element={<Settings />} />
          <Route path="/settings/team" element={<Settings />} />
        </Route>
        <Route
          element={
            <PrivateRoute adminOnly>
              <Audit />
            </PrivateRoute>
          }
        >
          <Route path="/audit" element={<Audit />} />
        </Route>
      </Route>
      <Route path="/" element={<Navigate to="/inbox" replace />} />
      <Route path="*" element={<Navigate to="/inbox" replace />} />
    </Routes>
  );
}

export default App;