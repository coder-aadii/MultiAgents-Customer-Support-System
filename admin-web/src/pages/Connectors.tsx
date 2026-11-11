import React, { useState, useEffect } from "react";
import {
  getConnectors,
  createConnector,
  updateConnector,
  deleteConnector,
  testConnector,
  type ConnectorItem,
  type ConnectorTestResult,
} from "../api/client";
import { useNotification } from "../context/NotificationContext";

export default function Connectors() {
  const notify = useNotification();
  const [connectors, setConnectors] = useState<ConnectorItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id?: string; result: ConnectorTestResult } | null>(null);

  // Form state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [connectorType, setConnectorType] = useState<"rest" | "sql" | "graphql">("rest");
  const [baseUrl, setBaseUrl] = useState("");
  const [authType, setAuthType] = useState("bearer");
  const [secretReference, setSecretReference] = useState("REST_API_KEY");
  const [configJsonStr, setConfigJsonStr] = useState("{\n  \"operations\": {}\n}");
  const [formTesting, setFormTesting] = useState(false);
  const [formTestResult, setFormTestResult] = useState<ConnectorTestResult | null>(null);
  const [saving, setSaving] = useState(false);

  const fetchConnectors = async () => {
    try {
      setLoading(true);
      const data = await getConnectors();
      setConnectors(data);
    } catch (err: any) {
      notify.error(err.response?.data?.detail || "Failed to load external connectors");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConnectors();
  }, []);

  const openCreateModal = () => {
    setEditingId(null);
    setName("");
    setConnectorType("rest");
    setBaseUrl("");
    setAuthType("bearer");
    setSecretReference("REST_API_KEY");
    setConfigJsonStr("{\n  \"operations\": {}\n}");
    setFormTestResult(null);
    setModalOpen(true);
  };

  const openEditModal = (c: ConnectorItem) => {
    setEditingId(c.id);
    setName(c.name);
    setConnectorType(c.connector_type);
    setBaseUrl(c.base_url || "");
    setAuthType(c.auth_type);
    setSecretReference(c.secret_reference);
    setConfigJsonStr(JSON.stringify(c.config_json || {}, null, 2));
    setFormTestResult(null);
    setModalOpen(true);
  };

  const handleTestInModal = async () => {
    if (!baseUrl.trim()) {
      notify.warning("Please enter a Base URL to test");
      return;
    }
    setFormTesting(true);
    setFormTestResult(null);
    try {
      const res = await testConnector({
        base_url: baseUrl.trim(),
        auth_type: authType,
        secret_reference: secretReference.trim(),
      });
      setFormTestResult(res);
      if (res.success) {
        notify.success(`Ping successful (${res.latency_ms.toFixed(1)}ms)`);
      } else {
        notify.error(`Test failed: ${res.message}`);
      }
    } catch (err: any) {
      setFormTestResult({
        success: false,
        message: err.response?.data?.detail || err.message,
        latency_ms: 0,
        error_type: "NETWORK_ERROR",
      });
      notify.error(err.response?.data?.detail || "Connection test failed");
    } finally {
      setFormTesting(false);
    }
  };

  const handleTestRow = async (c: ConnectorItem) => {
    if (!c.base_url) return;
    setTestingId(c.id);
    setTestResult(null);
    try {
      const res = await testConnector({
        base_url: c.base_url,
        auth_type: c.auth_type,
        secret_reference: c.secret_reference,
      });
      setTestResult({ id: c.id, result: res });
      if (res.success) {
        notify.success(`Connected to ${c.name} (${res.latency_ms.toFixed(1)}ms)`);
      } else {
        notify.error(`Failed ${c.name}: ${res.message}`);
      }
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message;
      setTestResult({
        id: c.id,
        result: { success: false, message: msg, latency_ms: 0, error_type: "ERROR" },
      });
      notify.error(msg);
    } finally {
      setTestingId(null);
    }
  };

  const handleToggleEnabled = async (c: ConnectorItem) => {
    try {
      const updated = await updateConnector(c.id, { is_enabled: !c.is_enabled });
      setConnectors((prev) => prev.map((item) => (item.id === c.id ? updated : item)));
      notify.success(`Connector ${c.name} ${updated.is_enabled ? "enabled" : "disabled"}`);
    } catch (err: any) {
      notify.error(err.response?.data?.detail || "Failed to update connector status");
    }
  };

  const handleDelete = async (c: ConnectorItem) => {
    if (!window.confirm(`Are you sure you want to delete connector '${c.name}'?`)) return;
    try {
      await deleteConnector(c.id);
      setConnectors((prev) => prev.filter((item) => item.id !== c.id));
      notify.success(`Connector '${c.name}' deleted`);
    } catch (err: any) {
      notify.error(err.response?.data?.detail || "Failed to delete connector");
    }
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    let parsedConfig = {};
    if (configJsonStr.trim()) {
      try {
        parsedConfig = JSON.parse(configJsonStr);
      } catch (err) {
        notify.error("Config JSON is not valid JSON syntax");
        return;
      }
    }

    setSaving(true);
    try {
      if (editingId) {
        const updated = await updateConnector(editingId, {
          name: name.trim(),
          connector_type: connectorType,
          base_url: baseUrl.trim() || null,
          auth_type: authType,
          secret_reference: secretReference.trim(),
          config_json: parsedConfig,
        });
        setConnectors((prev) => prev.map((item) => (item.id === editingId ? updated : item)));
        notify.success("Connector updated successfully");
      } else {
        const created = await createConnector({
          name: name.trim(),
          connector_type: connectorType,
          base_url: baseUrl.trim() || null,
          auth_type: authType,
          secret_reference: secretReference.trim(),
          config_json: parsedConfig,
        });
        setConnectors((prev) => [created, ...prev]);
        notify.success("Connector created successfully");
      }
      setModalOpen(false);
    } catch (err: any) {
      notify.error(err.response?.data?.detail || "Failed to save connector");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="sm:flex sm:items-center sm:justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Enterprise Data Connectors</h1>
          <p className="mt-1 text-sm text-gray-500">
            Configure external REST, SQL, and GraphQL connectors with deny-by-default SSRF protection.
          </p>
        </div>
        <div className="mt-4 sm:mt-0">
          <button
            onClick={openCreateModal}
            className="inline-flex items-center px-4 py-2 border border-transparent rounded-md shadow-xs text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
          >
            + Register Connector
          </button>
        </div>
      </div>

      {/* Security Invariant Callout */}
      <div className="mb-6 bg-blue-50 border-l-4 border-blue-500 p-4 rounded-r-md">
        <div className="flex">
          <div className="shrink-0 text-blue-500">🛡️</div>
          <div className="ml-3 text-sm text-blue-800">
            <span className="font-semibold">SSRF Protection Gate Active:</span> Outbound requests targeting loopback (<code>127.0.0.0/8</code>), private subnets (<code>10.0.0.0/8</code>, <code>172.16.0.0/12</code>, <code>192.168.0.0/16</code>), or cloud metadata services (<code>169.254.169.254</code>) are blocked fail-closed before network dispatch.
          </div>
        </div>
      </div>

      {/* Connectors Table */}
      <div className="bg-white shadow-xs rounded-lg overflow-hidden border border-gray-200">
        {loading ? (
          <div className="p-8 text-center text-gray-500">Loading connectors...</div>
        ) : connectors.length === 0 ? (
          <div className="p-12 text-center">
            <p className="text-gray-500 text-base mb-4">No external connectors configured yet.</p>
            <button
              onClick={openCreateModal}
              className="inline-flex items-center px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-md"
            >
              Register your first connector
            </button>
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Name & Type
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Base URL / Target
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Auth & Secret
                </th>
                <th className="px-6 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Status
                </th>
                <th className="px-6 py-3 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {connectors.map((c) => {
                const rowResult = testResult?.id === c.id ? testResult.result : null;
                return (
                  <tr key={c.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="font-semibold text-gray-900">{c.name}</div>
                      <div className="text-xs text-gray-500 uppercase tracking-wider">
                        <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                          {c.connector_type}
                        </span>
                      </div>
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-700">
                      {c.base_url ? (
                        <div className="font-mono text-xs text-gray-900 max-w-xs truncate" title={c.base_url}>
                          {c.base_url}
                        </div>
                      ) : (
                        <span className="text-gray-400 italic">None</span>
                      )}
                      {rowResult && (
                        <div
                          className={`mt-1 text-xs font-medium ${
                            rowResult.success ? "text-green-600" : "text-red-600"
                          }`}
                        >
                          {rowResult.success
                            ? `✓ Connected (${rowResult.latency_ms.toFixed(1)}ms)`
                            : `✕ ${rowResult.message}`}
                        </div>
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-xs text-gray-600">
                      <div>Auth: <span className="font-medium text-gray-900">{c.auth_type}</span></div>
                      <div>Secret: <span className="font-mono text-gray-700">{c.secret_reference}</span></div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm">
                      <button
                        onClick={() => handleToggleEnabled(c)}
                        className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium cursor-pointer ${
                          c.is_enabled
                            ? "bg-green-100 text-green-800 hover:bg-green-200"
                            : "bg-gray-100 text-gray-800 hover:bg-gray-200"
                        }`}
                      >
                        {c.is_enabled ? "Enabled" : "Disabled"}
                      </button>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium space-x-2">
                      {c.base_url && (
                        <button
                          onClick={() => handleTestRow(c)}
                          disabled={testingId === c.id}
                          className="text-xs px-2.5 py-1 rounded border border-gray-300 text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                        >
                          {testingId === c.id ? "Pinging..." : "Test"}
                        </button>
                      )}
                      <button
                        onClick={() => openEditModal(c)}
                        className="text-xs px-2.5 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-50"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(c)}
                        className="text-xs px-2.5 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Modal: Register / Edit Connector */}
      {modalOpen && (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-black bg-opacity-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-lg max-w-xl w-full p-6 shadow-xl border border-gray-200">
            <h2 className="text-xl font-bold text-gray-900 mb-4">
              {editingId ? "Edit External Connector" : "Register External Connector"}
            </h2>

            <form onSubmit={handleSave} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                  Connector Name *
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. rest_cloudpulse"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                    Connector Type
                  </label>
                  <select
                    value={connectorType}
                    onChange={(e: any) => setConnectorType(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="rest">REST API</option>
                    <option value="sql">SQL Database</option>
                    <option value="graphql">GraphQL API</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                    Auth Type
                  </label>
                  <select
                    value={authType}
                    onChange={(e) => setAuthType(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="bearer">Bearer Token</option>
                    <option value="api_key">API Key Header</option>
                    <option value="basic">HTTP Basic</option>
                    <option value="none">None (Public)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                  Base URL (Target Remote Host)
                </label>
                <div className="flex gap-2">
                  <input
                    type="url"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://api.external-service.com"
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <button
                    type="button"
                    onClick={handleTestInModal}
                    disabled={formTesting || !baseUrl.trim()}
                    className="px-3 py-2 border border-gray-300 text-xs font-semibold rounded-md text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                  >
                    {formTesting ? "Testing..." : "Test SSRF & Ping"}
                  </button>
                </div>
                <p className="mt-1 text-xs text-gray-500">
                  SSRF gate denies private RFC 1918 subnets, 127.0.0.1, and 169.254.169.254.
                </p>

                {formTestResult && (
                  <div
                    className={`mt-2 p-2 rounded text-xs border ${
                      formTestResult.success
                        ? "bg-green-50 border-green-200 text-green-800"
                        : "bg-red-50 border-red-200 text-red-800"
                    }`}
                  >
                    <div className="font-semibold">
                      {formTestResult.success ? "✓ Valid Connection" : "✕ Test Failed"}
                    </div>
                    <div>{formTestResult.message}</div>
                    {formTestResult.latency_ms > 0 && (
                      <div className="text-[11px] text-gray-500">
                        Latency: {formTestResult.latency_ms.toFixed(1)}ms
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                  Secret Reference Key (Env Variable)
                </label>
                <input
                  type="text"
                  value={secretReference}
                  onChange={(e) => setSecretReference(e.target.value)}
                  placeholder="e.g. CLOUDPULSE_API_KEY"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-gray-700 uppercase mb-1">
                  Config JSON (Operations & Allowed Fields)
                </label>
                <textarea
                  rows={5}
                  value={configJsonStr}
                  onChange={(e) => setConfigJsonStr(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-xs font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-gray-200">
                <button
                  type="button"
                  onClick={() => setModalOpen(false)}
                  className="px-4 py-2 border border-gray-300 rounded-md text-sm text-gray-700 hover:bg-gray-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-semibold hover:bg-blue-700 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Save Connector"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

