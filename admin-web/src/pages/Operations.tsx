import { useEffect, useState, useCallback } from "react";
import { operationsApi, apiClient, type AgentRosterItem } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useNotification } from "../context/NotificationContext";

export default function Operations() {
  const { user } = useAuth();
  const notify = useNotification();
  const [realtime, setRealtime] = useState<any>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [teams, setTeams] = useState<any[]>([]);
  const [agents, setAgents] = useState<AgentRosterItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const promises: [Promise<any>, Promise<any>, Promise<any>, Promise<AgentRosterItem[]>] = [
        operationsApi.getRealtimeAnalytics(),
        operationsApi.getMetrics(),
        operationsApi.listTeams(),
        user?.role === "admin"
          ? operationsApi.listAgents().catch(() => [] as AgentRosterItem[])
          : Promise.resolve([] as AgentRosterItem[]),
      ];

      const [rtData, metData, teamsData, agentsData] = await Promise.all(promises);
      setRealtime(rtData);
      setMetrics(metData);
      setTeams(teamsData);
      setAgents(agentsData);
    } catch (err: any) {
      notify.error(err?.message || "Failed to load operational metrics");
    } finally {
      setLoading(false);
    }
  }, [user?.role, notify]);

  useEffect(() => {
    fetchData();

    const handlePresenceUpdate = (e: any) => {
      const { presence: newPresence, userId } = e.detail || {};
      if (newPresence && userId) {
        setAgents((prev) =>
          prev.map((ag) => (ag.id === userId ? { ...ag, presence: newPresence } : ag))
        );
      }
    };

    window.addEventListener("agent-presence-changed", handlePresenceUpdate);
    return () => {
      window.removeEventListener("agent-presence-changed", handlePresenceUpdate);
    };
  }, [fetchData]);

  const handleRunSLAScan = async () => {
    try {
      setScanning(true);
      const res = await apiClient.post("/admin/operations/sla-scan");
      notify.success(`SLA scan completed. Evaluated ${res.data?.length || 0} active conversations.`);
      await fetchData();
    } catch (err: any) {
      notify.error(err?.message || "Failed to execute scan");
    } finally {
      setScanning(false);
    }
  };

  if (loading && !realtime) {
    return (
      <div className="p-8 text-center text-gray-500 font-medium">
        Loading operational analytics & queues...
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-gray-200">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Support Operations & SLA Hub</h1>
          <p className="text-sm text-gray-500 mt-1">
            Real-time queue depths, agent workloads, SLA compliance, and customer satisfaction metrics.
          </p>
        </div>
        <div className="mt-4 sm:mt-0 flex items-center space-x-3">
          <button
            type="button"
            onClick={handleRunSLAScan}
            disabled={scanning}
            className="px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700 disabled:opacity-50 transition-colors shadow-xs"
          >
            {scanning ? "Scanning SLAs..." : "Run SLA Scan Now"}
          </button>
          <button
            type="button"
            onClick={fetchData}
            className="px-3 py-2 border border-gray-300 rounded-md text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <div className="bg-white p-5 rounded-lg border border-gray-200 shadow-xs">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Total Open Tickets
          </span>
          <div className="text-3xl font-bold text-gray-900 mt-2">
            {realtime?.total_open_tickets ?? realtime?.active_chats ?? 0}
          </div>
          <span className="text-xs text-gray-500 mt-1 block">Active staff & visitor chats</span>
        </div>

        <div className="bg-white p-5 rounded-lg border border-gray-200 shadow-xs">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Waiting in Queue
          </span>
          <div className="text-3xl font-bold text-amber-600 mt-2">
            {realtime?.waiting_for_staff ?? 0}
          </div>
          <span className="text-xs text-gray-500 mt-1 block">Awaiting staff assignment</span>
        </div>

        <div className="bg-white p-5 rounded-lg border border-gray-200 shadow-xs">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Assigned Agents
          </span>
          <div className="text-3xl font-bold text-indigo-600 mt-2">
            {realtime?.assigned_agents ?? 0}
          </div>
          <span className="text-xs text-gray-500 mt-1 block">Staff handling active tickets</span>
        </div>

        <div className="bg-white p-5 rounded-lg border border-gray-200 shadow-xs">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            Active SLA Breaches
          </span>
          <div className="text-3xl font-bold text-red-600 mt-2">
            {realtime?.active_sla_breaches ?? realtime?.sla_breaches ?? 0}
          </div>
          <span className="text-xs text-gray-500 mt-1 block">Exceeded response threshold</span>
        </div>

        <div className="bg-white p-5 rounded-lg border border-gray-200 shadow-xs">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
            CSAT Rating
          </span>
          <div className="text-3xl font-bold text-emerald-600 mt-2">
            {metrics?.average_csat_score ? `${metrics.average_csat_score} ★` : "—"}
          </div>
          <span className="text-xs text-gray-500 mt-1 block">
            From {metrics?.total_csat_surveys ?? 0} surveys
          </span>
        </div>
      </div>

      {/* Row 2: Queue Depths & SLA Performance */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Queue Depths */}
        <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-xs space-y-4">
          <h2 className="text-base font-bold text-gray-900">Support Teams & Routing Queues</h2>
          {realtime?.queues && realtime.queues.length > 0 ? (
            <div className="divide-y divide-gray-100">
              {realtime.queues.map((q: any) => (
                <div key={q.team_id} className="py-3 flex items-center justify-between">
                  <div>
                    <span className="font-semibold text-sm text-gray-800">{q.team_name}</span>
                  </div>
                  <div className="flex items-center space-x-2">
                    <span className="text-xs px-2.5 py-1 rounded-full font-bold bg-indigo-50 text-indigo-700">
                      {q.depth} tickets
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : teams.length > 0 ? (
            <div className="divide-y divide-gray-100">
              {teams.map((t: any) => (
                <div key={t.id} className="py-2.5 flex items-center justify-between">
                  <div>
                    <span className="font-semibold text-sm text-gray-800">{t.name}</span>
                    {t.description && <p className="text-xs text-gray-400">{t.description}</p>}
                  </div>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">0 active</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-gray-500 py-4">No specialized teams configured yet.</div>
          )}
        </div>

        {/* Historical Averages & CSAT Distribution */}
        <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-xs space-y-4">
          <h2 className="text-base font-bold text-gray-900">Historical Operational KPIs</h2>
          <div className="grid grid-cols-2 gap-4 pb-4 border-b border-gray-100">
            <div>
              <span className="text-xs text-gray-500">Avg First Response Time</span>
              <div className="text-xl font-bold text-gray-800 mt-1">
                {metrics?.average_first_response_minutes ?? 0} min
              </div>
            </div>
            <div>
              <span className="text-xs text-gray-500">Avg Resolution Time</span>
              <div className="text-xl font-bold text-gray-800 mt-1">
                {metrics?.average_resolution_minutes ?? 0} min
              </div>
            </div>
          </div>

          <div>
            <span className="text-xs font-semibold text-gray-600 block mb-2">
              CSAT Rating Distribution
            </span>
            <div className="space-y-1.5">
              {[5, 4, 3, 2, 1].map((stars) => {
                const count = metrics?.csat_distribution?.[stars] ?? 0;
                const total = metrics?.total_csat_surveys || 1;
                const pct = Math.round((count / total) * 100);
                return (
                  <div key={stars} className="flex items-center text-xs space-x-2">
                    <span className="w-12 text-gray-600">{stars} Stars</span>
                    <div className="flex-1 bg-gray-100 rounded-full h-3 overflow-hidden">
                      <div
                        className="bg-emerald-500 h-full rounded-full transition-all"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="w-8 text-right font-mono text-gray-500">{count}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Row 3: Human Agent Roster & Live Status (Restricted to Admins) */}
      {user?.role === "admin" && (
        <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-xs space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-base font-bold text-gray-900">Human Agents & Staff Roster</h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Live presence status, active concurrent chat workloads, and team assignments.
              </p>
            </div>
            <span className="text-xs font-semibold px-2.5 py-1 bg-gray-100 text-gray-700 rounded-full">
              {agents.length} Total Staff
            </span>
          </div>

          {agents.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                <thead className="bg-gray-50 text-xs font-semibold text-gray-500 uppercase">
                  <tr>
                    <th scope="col" className="px-4 py-3">Agent</th>
                    <th scope="col" className="px-4 py-3">Role</th>
                    <th scope="col" className="px-4 py-3">Presence</th>
                    <th scope="col" className="px-4 py-3">Active Chats</th>
                    <th scope="col" className="px-4 py-3">Teams</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {agents.map((ag) => (
                    <tr key={ag.id} className="hover:bg-gray-50/80 transition-colors">
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">{ag.display_name}</div>
                        <div className="text-xs text-gray-500">{ag.email}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`px-2 py-0.5 text-xs font-semibold rounded uppercase ${
                            ag.role === "admin"
                              ? "bg-purple-100 text-purple-800"
                              : "bg-blue-100 text-blue-800"
                          }`}
                        >
                          {ag.role}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full ${
                            ag.presence === "online"
                              ? "bg-green-50 text-green-700 border border-green-200"
                              : ag.presence === "busy"
                              ? "bg-red-50 text-red-700 border border-red-200"
                              : ag.presence === "away"
                              ? "bg-yellow-50 text-yellow-700 border border-yellow-200"
                              : "bg-gray-100 text-gray-600 border border-gray-200"
                          }`}
                        >
                          <span
                            className={`w-2 h-2 rounded-full ${
                              ag.presence === "online"
                                ? "bg-green-500"
                                : ag.presence === "busy"
                                ? "bg-red-500"
                                : ag.presence === "away"
                                ? "bg-yellow-500"
                                : "bg-gray-400"
                            }`}
                          />
                          {ag.presence.charAt(0).toUpperCase() + ag.presence.slice(1)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`font-semibold ${
                            ag.active_chats > 0 ? "text-indigo-600" : "text-gray-500"
                          }`}
                        >
                          {ag.active_chats} active
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {ag.teams.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {ag.teams.map((t, i) => (
                              <span
                                key={i}
                                className="px-2 py-0.5 text-xs bg-gray-100 text-gray-700 rounded font-medium"
                              >
                                {t}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-gray-400 italic">General Pool</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-sm text-gray-500 py-4 text-center">
              No agents found.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
