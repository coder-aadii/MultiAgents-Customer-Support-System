import { Routes, Route, Navigate } from "react-router-dom";
import { ProtectedRoute } from "./components/ProtectedRoute";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Inbox from "./pages/Inbox";
import Operations from "./pages/Operations";
import PendingActions from "./pages/PendingActions";
import Knowledge from "./pages/Knowledge";
import Settings from "./pages/Settings";
import Audit from "./pages/Audit";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route
          path="/inbox"
          element={
            <ProtectedRoute title="Inbox">
              <Inbox />
            </ProtectedRoute>
          }
        />
        <Route
          path="/operations"
          element={
            <ProtectedRoute title="Support Operations & SLA">
              <Operations />
            </ProtectedRoute>
          }
        />
        <Route
          path="/actions"
          element={
            <ProtectedRoute title="Supervisor Action Queue">
              <PendingActions />
            </ProtectedRoute>
          }
        />
        <Route
          path="/knowledge"
          element={
            <ProtectedRoute title="Knowledge Base">
              <Knowledge />
            </ProtectedRoute>
          }
        />
        <Route
          path="/knowledge/new"
          element={
            <ProtectedRoute title="New Document">
              <Knowledge />
            </ProtectedRoute>
          }
        />
        <Route
          path="/knowledge/:id"
          element={
            <ProtectedRoute title="Document Detail">
              <Knowledge />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings/widget"
          element={
            <ProtectedRoute adminOnly title="Widget Settings">
              <Settings />
            </ProtectedRoute>
          }
        />
        <Route
          path="/settings/team"
          element={
            <ProtectedRoute adminOnly title="Team Settings">
              <Settings />
            </ProtectedRoute>
          }
        />
        <Route
          path="/audit"
          element={
            <ProtectedRoute adminOnly title="Audit Log">
              <Audit />
            </ProtectedRoute>
          }
        />
      </Route>
      <Route path="/" element={<Navigate to="/inbox" replace />} />
      <Route path="*" element={<Navigate to="/inbox" replace />} />
    </Routes>
  );
}
