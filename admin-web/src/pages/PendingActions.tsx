import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { actionsApi, type BusinessActionItem } from "../api/client";
import { useNotification } from "../context/NotificationContext";

export default function PendingActions() {
  const notify = useNotification();
  const [actions, setActions] = useState<BusinessActionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAction, setSelectedAction] = useState<BusinessActionItem | null>(null);
  const [notes, setNotes] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [processing, setProcessing] = useState(false);

  const fetchPending = async () => {
    try {
      setLoading(true);
      setError(null);
      const items = await actionsApi.listPending();
      setActions(items);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Failed to load pending actions");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPending();
    const interval = setInterval(fetchPending, 15000);
    return () => clearInterval(interval);
  }, []);

  const handleApprove = async (action: BusinessActionItem) => {
    try {
      setProcessing(true);
      await actionsApi.approve(action.id, notes.trim() || undefined);
      notify.success(`Action approved successfully (${action.action_name})`, "Approved");
      setSelectedAction(null);
      setNotes("");
      await fetchPending();
    } catch (err: any) {
      notify.error(err?.response?.data?.detail || "Approval failed", "Error");
    } finally {
      setProcessing(false);
    }
  };

  const handleReject = async (action: BusinessActionItem) => {
    if (!rejectReason.trim()) {
      notify.warning("Please provide a rejection reason.", "Reason Required");
      return;
    }
    try {
      setProcessing(true);
      await actionsApi.reject(action.id, rejectReason.trim());
      notify.info(`Action rejected (${action.action_name})`, "Rejected");
      setSelectedAction(null);
      setRejectReason("");
      await fetchPending();
    } catch (err: any) {
      notify.error(err?.response?.data?.detail || "Rejection failed", "Error");
    } finally {
      setProcessing(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Supervisor Action Queue</h1>
          <p className="text-sm text-gray-500 mt-1">
            Review and authorize high-risk customer business actions requiring dual-authorization.
          </p>
        </div>
        <button
          onClick={fetchPending}
          disabled={loading}
          className="px-3 py-1.5 text-sm bg-white border border-gray-300 rounded-md shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {loading ? "Refreshing..." : "Refresh Queue"}
        </button>
      </div>

      {error && (
        <div className="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
          {error}
        </div>
      )}

      {loading && actions.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center text-gray-500">
          Loading pending actions...
        </div>
      ) : actions.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center text-gray-500">
          <svg
            className="mx-auto h-12 w-12 text-green-400 mb-3"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <p className="text-base font-medium text-gray-900">No actions pending review</p>
          <p className="text-sm text-gray-500 mt-1">All high-risk customer requests have been processed.</p>
        </div>
      ) : (
        <div className="bg-white shadow overflow-hidden sm:rounded-md border border-gray-200">
          <ul role="list" className="divide-y divide-gray-200">
            {actions.map((act) => (
              <li key={act.id} className="p-4 sm:p-6 hover:bg-gray-50 transition-colors">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase ${
                          act.risk_level === "critical"
                            ? "bg-red-100 text-red-800 border border-red-200"
                            : act.risk_level === "high"
                            ? "bg-amber-100 text-amber-800 border border-amber-200"
                            : "bg-blue-100 text-blue-800"
                        }`}
                      >
                        {act.risk_level} Risk
                      </span>
                      <span className="font-semibold text-gray-900">{act.action_name}</span>
                    </div>
                    <p className="text-sm font-medium text-gray-800">{act.summary_message}</p>
                    <div className="flex flex-wrap gap-4 text-xs text-gray-500 pt-1">
                      <span>Customer: <strong className="text-gray-700">{act.customer_id}</strong></span>
                      <span>
                        Conversation:{" "}
                        <Link
                          to={`/inbox?conversationId=${act.conversation_id}`}
                          className="text-blue-600 hover:underline"
                        >
                          {act.conversation_id.slice(0, 8)}...
                        </Link>
                      </span>
                      <span>Expires: {new Date(act.expires_at).toLocaleTimeString()}</span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2 sm:self-center">
                    <button
                      onClick={() => setSelectedAction(act)}
                      className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      Review & Decide
                    </button>
                  </div>
                </div>

                {/* Inline Decision Drawer */}
                {selectedAction?.id === act.id && (
                  <div className="mt-4 pt-4 border-t border-gray-200 bg-gray-50 p-4 rounded-md space-y-4">
                    <div>
                      <h4 className="text-xs font-bold text-gray-600 uppercase tracking-wider mb-1">
                        Payload Parameters
                      </h4>
                      <pre className="text-xs bg-white p-2.5 rounded border border-gray-200 font-mono overflow-x-auto">
                        {JSON.stringify(act.payload_json, null, 2)}
                      </pre>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div className="space-y-2">
                        <label className="block text-xs font-semibold text-gray-700">
                          Approval Notes (Optional)
                        </label>
                        <input
                          type="text"
                          value={notes}
                          onChange={(e) => setNotes(e.target.value)}
                          placeholder="e.g. Verified customer tenure"
                          className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500"
                        />
                        <button
                          onClick={() => handleApprove(act)}
                          disabled={processing}
                          className="w-full py-2 bg-green-600 hover:bg-green-700 text-white font-medium text-sm rounded shadow-sm disabled:opacity-50"
                        >
                          {processing ? "Processing..." : "Approve & Execute"}
                        </button>
                      </div>

                      <div className="space-y-2">
                        <label className="block text-xs font-semibold text-gray-700">
                          Rejection Reason (Required to Reject)
                        </label>
                        <input
                          type="text"
                          value={rejectReason}
                          onChange={(e) => setRejectReason(e.target.value)}
                          placeholder="e.g. Return window expired"
                          className="w-full text-sm border-gray-300 rounded-md shadow-sm focus:ring-red-500 focus:border-red-500"
                        />
                        <button
                          onClick={() => handleReject(act)}
                          disabled={processing || !rejectReason.trim()}
                          className="w-full py-2 bg-red-600 hover:bg-red-700 text-white font-medium text-sm rounded shadow-sm disabled:opacity-50"
                        >
                          {processing ? "Processing..." : "Reject Action"}
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
