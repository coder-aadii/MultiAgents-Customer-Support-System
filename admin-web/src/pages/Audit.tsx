import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "../hooks/useTranslation";
import { getAuditEvents, type AuditEventItem } from "../api/client";
import { toSafeErrorMessage } from "../utils/errors";
import { LoadingState, EmptyState, ErrorState } from "../components/common/UIStates";

export default function Audit() {
  const { t } = useTranslation();

  const [events, setEvents] = useState<AuditEventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Pagination state (cursor stack for backward/forward navigation)
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);

  // Filters
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [resourceTypeFilter, setResourceTypeFilter] = useState("");
  const [selectedEvent, setSelectedEvent] = useState<AuditEventItem | null>(null);

  const fetchEvents = useCallback(async (targetCursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAuditEvents({
        event_type: eventTypeFilter.trim() || undefined,
        resource_type: resourceTypeFilter.trim() || undefined,
        cursor: targetCursor,
        limit: 20,
      });
      setEvents(data.items);
      setNextCursor(data.next_cursor);
      setHasMore(data.has_more);
    } catch (err) {
      setError(toSafeErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [eventTypeFilter, resourceTypeFilter]);

  useEffect(() => {
    // Reset pagination when filters change
    setCursor(undefined);
    setCursorHistory([]);
    fetchEvents(undefined);
  }, [fetchEvents]);

  const handleNextPage = () => {
    if (nextCursor) {
      setCursorHistory((prev) => [...prev, cursor || ""]);
      setCursor(nextCursor);
      fetchEvents(nextCursor);
    }
  };

  const handlePreviousPage = () => {
    if (cursorHistory.length > 0) {
      const prevHistory = [...cursorHistory];
      const prevCursor = prevHistory.pop();
      setCursorHistory(prevHistory);
      const effectiveCursor = prevCursor === "" ? undefined : prevCursor;
      setCursor(effectiveCursor);
      fetchEvents(effectiveCursor);
    }
  };

  const getActorBadge = (actorType: string) => {
    switch (actorType) {
      case "user":
        return "bg-blue-100 text-blue-800 border-blue-200";
      case "visitor":
        return "bg-purple-100 text-purple-800 border-purple-200";
      case "system":
        return "bg-gray-100 text-gray-800 border-gray-200";
      default:
        return "bg-gray-50 text-gray-600 border-gray-200";
    }
  };

  const getEventBadge = (eventType: string) => {
    if (eventType.startsWith("auth.")) return "bg-amber-50 text-amber-700 border-amber-200";
    if (eventType.startsWith("tool.")) return "bg-emerald-50 text-emerald-700 border-emerald-200";
    if (eventType.startsWith("handoff.")) return "bg-rose-50 text-rose-700 border-rose-200";
    return "bg-sky-50 text-sky-700 border-sky-200";
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{t("audit.title")}</h1>
          <p className="text-sm text-gray-500 mt-1">
            Immutable platform and security telemetry journal for audits and compliance.
          </p>
        </div>
        <button
          type="button"
          onClick={() => fetchEvents(cursor)}
          className="inline-flex items-center px-3 py-2 border border-gray-300 shadow-sm text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50 focus:outline-none"
        >
          ↻ Refresh
        </button>
      </div>

      {/* Filter Bar */}
      <div className="bg-white rounded-lg border border-gray-200 p-4 shadow-sm flex flex-wrap gap-4 items-center">
        <div className="w-full sm:w-64">
          <label htmlFor="filterEventType" className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">
            {t("audit.eventType")}
          </label>
          <input
            id="filterEventType"
            type="text"
            value={eventTypeFilter}
            onChange={(e) => setEventTypeFilter(e.target.value)}
            placeholder="e.g. auth.login, message.create"
            className="w-full rounded-md border-gray-300 shadow-sm text-sm p-2 border focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        <div className="w-full sm:w-64">
          <label htmlFor="filterResourceType" className="block text-xs font-medium text-gray-500 uppercase tracking-wider mb-1">
            {t("audit.resource")}
          </label>
          <input
            id="filterResourceType"
            type="text"
            value={resourceTypeFilter}
            onChange={(e) => setResourceTypeFilter(e.target.value)}
            placeholder="e.g. conversation, message, tool"
            className="w-full rounded-md border-gray-300 shadow-sm text-sm p-2 border focus:ring-blue-500 focus:border-blue-500"
          />
        </div>
        {(eventTypeFilter || resourceTypeFilter) && (
          <div className="self-end pb-1">
            <button
              type="button"
              onClick={() => {
                setEventTypeFilter("");
                setResourceTypeFilter("");
              }}
              className="text-xs text-blue-600 hover:text-blue-800 font-medium underline"
            >
              Clear filters
            </button>
          </div>
        )}
      </div>

      {/* Audit Log Table */}
      <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
        {loading && events.length === 0 ? (
          <div className="p-8">
            <LoadingState message="Loading audit events..." />
          </div>
        ) : error ? (
          <div className="p-8">
            <ErrorState message={error} onRetry={() => fetchEvents(cursor)} />
          </div>
        ) : events.length === 0 ? (
          <div className="p-8">
            <EmptyState description="No security audit events match the selected filters." />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
              <thead className="bg-gray-50 text-xs font-medium text-gray-500 uppercase tracking-wider">
                <tr>
                  <th scope="col" className="px-6 py-3">{t("audit.timestamp")}</th>
                  <th scope="col" className="px-6 py-3">{t("audit.eventType")}</th>
                  <th scope="col" className="px-6 py-3">{t("audit.actor")}</th>
                  <th scope="col" className="px-6 py-3">{t("audit.resource")}</th>
                  <th scope="col" className="px-6 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {events.map((ev) => (
                  <tr key={ev.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap text-gray-500 font-mono text-xs">
                      {new Date(ev.created_at).toLocaleString()}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getEventBadge(ev.event_type)}`}>
                        {ev.event_type}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="flex items-center space-x-2">
                        <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium border ${getActorBadge(ev.actor_type)}`}>
                          {ev.actor_type}
                        </span>
                        {ev.actor_id && (
                          <span className="text-gray-400 font-mono text-xs" title={ev.actor_id}>
                            {ev.actor_id.slice(0, 8)}...
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-gray-700">
                      <span className="font-medium text-xs text-gray-900">{ev.resource_type}</span>
                      {ev.resource_id && (
                        <span className="ml-1 text-gray-400 font-mono text-xs" title={ev.resource_id}>
                          #{ev.resource_id.slice(0, 8)}
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-xs font-medium">
                      <button
                        type="button"
                        onClick={() => setSelectedEvent(ev)}
                        className="text-blue-600 hover:text-blue-900 font-semibold"
                      >
                        View Details
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination Bar */}
        <div className="bg-gray-50 px-6 py-3 border-t border-gray-200 flex items-center justify-between">
          <div className="text-xs text-gray-500">
            Showing <span className="font-medium">{events.length}</span> events
          </div>
          <div className="flex space-x-2">
            <button
              type="button"
              disabled={cursorHistory.length === 0 || loading}
              onClick={handlePreviousPage}
              className="px-3 py-1.5 border border-gray-300 rounded-md text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={!hasMore || !nextCursor || loading}
              onClick={handleNextPage}
              className="px-3 py-1.5 border border-gray-300 rounded-md text-xs font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      </div>

      {/* Details Modal */}
      {selectedEvent && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 overflow-y-auto bg-black bg-opacity-50 flex items-center justify-center p-4"
        >
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6 space-y-4">
            <div className="flex items-center justify-between border-b pb-3">
              <h3 className="text-lg font-semibold text-gray-900">
                Audit Event Details
              </h3>
              <button
                type="button"
                onClick={() => setSelectedEvent(null)}
                className="text-gray-400 hover:text-gray-600 font-bold text-xl"
              >
                ×
              </button>
            </div>
            <div className="grid grid-cols-2 gap-4 text-xs">
              <div>
                <span className="font-medium text-gray-500">Event ID:</span>
                <p className="font-mono text-gray-800 break-all">{selectedEvent.id}</p>
              </div>
              <div>
                <span className="font-medium text-gray-500">Timestamp:</span>
                <p className="text-gray-800">{new Date(selectedEvent.created_at).toISOString()}</p>
              </div>
              <div>
                <span className="font-medium text-gray-500">Event Type:</span>
                <p className="font-semibold text-gray-800">{selectedEvent.event_type}</p>
              </div>
              <div>
                <span className="font-medium text-gray-500">Actor:</span>
                <p className="text-gray-800">{selectedEvent.actor_type} ({selectedEvent.actor_id || "none"})</p>
              </div>
              <div>
                <span className="font-medium text-gray-500">Resource:</span>
                <p className="text-gray-800">{selectedEvent.resource_type} ({selectedEvent.resource_id || "none"})</p>
              </div>
            </div>
            <div>
              <span className="block text-xs font-medium text-gray-500 mb-1">Sanitized Metadata:</span>
              <pre className="bg-gray-900 text-gray-100 rounded-md p-3 text-xs overflow-x-auto font-mono max-h-64">
                {JSON.stringify(selectedEvent.metadata || {}, null, 2)}
              </pre>
            </div>
            <div className="flex justify-end pt-2">
              <button
                type="button"
                onClick={() => setSelectedEvent(null)}
                className="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-800 text-sm font-medium rounded-md"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
