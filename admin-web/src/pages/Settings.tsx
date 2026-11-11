import { useState, useEffect, useCallback, type FormEvent } from "react";
import { useTranslation } from "../hooks/useTranslation";
import {
  getCurrentOrganization,
  updateCurrentOrganization,
  getWidgetSettings,
  updateWidgetSettings,
} from "../api/client";
import { toSafeErrorMessage } from "../utils/errors";
import { LoadingState, ErrorState } from "../components/common/UIStates";

export default function Settings() {
  const { t } = useTranslation();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // Form fields
  const [orgName, setOrgName] = useState("");
  const [orgSlug, setOrgSlug] = useState("");
  const [allowedOrigin, setAllowedOrigin] = useState("");
  const [welcomeMessage, setWelcomeMessage] = useState("");
  const [accentColor, setAccentColor] = useState("#2563eb");

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [orgData, widgetData] = await Promise.all([
        getCurrentOrganization(),
        getWidgetSettings(),
      ]);
      setOrgName(orgData.name || "");
      setOrgSlug(orgData.slug || "");
      setAllowedOrigin(widgetData.allowed_origin || "");
      setWelcomeMessage(widgetData.welcome_message || "");
      setAccentColor(widgetData.accent_color || "#2563eb");
    } catch (err) {
      setError(toSafeErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccessMessage(null);

    try {
      await Promise.all([
        updateCurrentOrganization({ name: orgName.trim() }),
        updateWidgetSettings({
          allowed_origin: allowedOrigin.trim(),
          welcome_message: welcomeMessage.trim(),
          accent_color: accentColor.trim(),
        }),
      ]);
      setSuccessMessage("Settings updated successfully.");
      setTimeout(() => setSuccessMessage(null), 4000);
    } catch (err) {
      setError(toSafeErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">{t("nav.settings")}</h1>
        <div className="bg-white rounded-lg border border-gray-200 p-8">
          <LoadingState message="Loading platform and widget settings..." />
        </div>
      </div>
    );
  }

  if (error && !orgName && !welcomeMessage) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">{t("nav.settings")}</h1>
        <div className="bg-white rounded-lg border border-gray-200 p-8">
          <ErrorState message={error} onRetry={loadSettings} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{t("nav.settings")}</h1>
        <p className="text-sm text-gray-500 mt-1">
          Manage your organization profile, allowed embed origins, and widget appearance.
        </p>
      </div>

      {successMessage && (
        <div
          role="status"
          aria-live="polite"
          className="p-4 rounded-md bg-green-50 border border-green-200 text-green-800 text-sm flex items-center justify-between"
        >
          <span>{successMessage}</span>
          <button
            type="button"
            onClick={() => setSuccessMessage(null)}
            className="text-green-600 hover:text-green-800 font-bold"
          >
            ×
          </button>
        </div>
      )}

      {error && (
        <div
          role="alert"
          aria-live="assertive"
          className="p-4 rounded-md bg-red-50 border border-red-200 text-red-800 text-sm flex items-center justify-between"
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => setError(null)}
            className="text-red-600 hover:text-red-800 font-bold"
          >
            ×
          </button>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Organization Profile Section */}
        <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Organization Profile</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="orgName" className="block text-sm font-medium text-gray-700">
                Organization Name
              </label>
              <input
                id="orgName"
                type="text"
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm p-2 border"
                placeholder="Acme Support"
              />
            </div>
            <div>
              <label htmlFor="orgSlug" className="block text-sm font-medium text-gray-700">
                Tenant Slug
              </label>
              <input
                id="orgSlug"
                type="text"
                disabled
                value={orgSlug}
                className="mt-1 block w-full rounded-md border-gray-200 bg-gray-50 text-gray-500 sm:text-sm p-2 border cursor-not-allowed"
              />
              <p className="mt-1 text-xs text-gray-500">Tenant identifier used for API and widget header routing.</p>
            </div>
          </div>
        </div>

        {/* Widget Customization Section */}
        <div className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm space-y-6">
          <h2 className="text-lg font-semibold text-gray-900">{t("settings.widget")}</h2>

          <div>
            <label htmlFor="allowedOrigin" className="block text-sm font-medium text-gray-700">
              {t("settings.allowedOrigin")}
            </label>
            <input
              id="allowedOrigin"
              type="text"
              required
              value={allowedOrigin}
              onChange={(e) => setAllowedOrigin(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm p-2 border"
              placeholder="http://localhost:5174 or https://support.example.com"
            />
            <p className="mt-1 text-xs text-gray-500">
              Comma-separated origins allowed to host and iframe the customer chat widget.
            </p>
          </div>

          <div>
            <label htmlFor="welcomeMessage" className="block text-sm font-medium text-gray-700">
              {t("settings.welcomeMessage")}
            </label>
            <textarea
              id="welcomeMessage"
              rows={3}
              required
              value={welcomeMessage}
              onChange={(e) => setWelcomeMessage(e.target.value)}
              className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm p-2 border"
              placeholder="Hi! How can MCSS Support help today?"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
            <div>
              <label htmlFor="accentColor" className="block text-sm font-medium text-gray-700">
                {t("settings.accentColor")}
              </label>
              <div className="mt-1 flex items-center space-x-3">
                <input
                  id="accentColor"
                  type="color"
                  value={accentColor}
                  onChange={(e) => setAccentColor(e.target.value)}
                  className="h-9 w-12 rounded border border-gray-300 cursor-pointer p-0.5"
                />
                <input
                  type="text"
                  value={accentColor}
                  onChange={(e) => setAccentColor(e.target.value)}
                  pattern="^#[0-9A-Fa-f]{6}$"
                  className="block w-28 rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm p-2 border font-mono"
                  placeholder="#2563eb"
                />
              </div>
            </div>

            {/* Live Widget Preview Card */}
            <div>
              <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">
                Live Preview
              </span>
              <div className="border border-gray-200 rounded-lg p-3 bg-gray-50 max-w-sm shadow-inner">
                <div
                  className="rounded-t-md px-3 py-2 text-white text-sm font-medium flex items-center justify-between"
                  style={{ backgroundColor: accentColor }}
                >
                  <span>{orgName || "Support"}</span>
                  <span className="text-xs opacity-80">● Online</span>
                </div>
                <div className="bg-white p-3 rounded-b-md border-t border-gray-100 text-xs text-gray-700 space-y-2">
                  <div className="bg-gray-100 rounded p-2 text-gray-800">
                    {welcomeMessage || "Hi! How can we help today?"}
                  </div>
                  <div
                    className="inline-block px-2.5 py-1 text-white rounded-full text-xs font-medium"
                    style={{ backgroundColor: accentColor }}
                  >
                    Start Chat
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end pt-2">
          <button
            type="submit"
            disabled={saving}
            className="inline-flex justify-center py-2 px-5 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:opacity-50"
          >
            {saving ? "Saving Changes..." : "Save Settings"}
          </button>
        </div>
      </form>
    </div>
  );
}
