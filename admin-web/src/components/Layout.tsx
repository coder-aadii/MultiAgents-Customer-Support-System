import { useState } from "react";
import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useTranslation } from "../hooks/useTranslation";

export default function Layout() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };

  const navItems = [
    { path: "/inbox", label: t("nav.inbox") },
    { path: "/knowledge", label: t("nav.knowledge") },
    { path: "/settings/widget", label: t("nav.settings"), adminOnly: true },
    { path: "/audit", label: t("nav.audit"), adminOnly: true },
  ];

  // Hide admin-only links when user is not admin
  const visibleNavItems = navItems.filter(
    (item) => !item.adminOnly || user?.role === "admin"
  );

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Accessible skip link */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:p-3 focus:bg-blue-600 focus:text-white focus:outline-none"
      >
        Skip to main content
      </a>

      {/* Semantic header landmark */}
      <header role="banner" className="bg-white border-b border-gray-200 sticky top-0 z-30 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center">
              <NavLink
                to="/inbox"
                className="text-2xl font-bold text-gray-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded"
              >
                MCSS
              </NavLink>
              <span className="hidden sm:inline-block ml-3 px-2 py-0.5 text-xs font-medium text-gray-500 bg-gray-100 rounded">
                {t("app.tagline")}
              </span>
            </div>

            {/* Desktop Navigation */}
            <nav aria-label="Main navigation" className="hidden md:flex items-center space-x-1">
              {visibleNavItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={({ isActive }) =>
                    `px-3 py-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                      isActive
                        ? "bg-blue-50 text-blue-700 font-semibold"
                        : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}

              <div className="flex items-center space-x-3 pl-4 ml-4 border-l border-gray-200">
                <span className="text-sm text-gray-700">
                  <span className="font-medium">{user?.display_name || user?.email}</span>{" "}
                  <span className="text-xs px-1.5 py-0.5 bg-gray-200 text-gray-700 rounded font-semibold uppercase">
                    {user?.role}
                  </span>
                </span>
                <button
                  type="button"
                  onClick={handleLogout}
                  className="px-2.5 py-1.5 text-sm font-medium text-gray-600 hover:text-gray-900 hover:bg-gray-100 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 transition-colors"
                >
                  {t("auth.logout")}
                </button>
              </div>
            </nav>

            {/* Mobile menu button */}
            <div className="md:hidden flex items-center">
              <button
                type="button"
                onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
                aria-expanded={mobileMenuOpen}
                aria-label="Toggle navigation menu"
                className="p-2 rounded-md text-gray-600 hover:text-gray-900 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  {mobileMenuOpen ? (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  ) : (
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                  )}
                </svg>
              </button>
            </div>
          </div>

          {/* Mobile dropdown navigation */}
          {mobileMenuOpen && (
            <nav aria-label="Mobile navigation" className="md:hidden py-3 border-t border-gray-200 space-y-1">
              {visibleNavItems.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  onClick={() => setMobileMenuOpen(false)}
                  className={({ isActive }) =>
                    `block px-3 py-2 rounded-md text-base font-medium ${
                      isActive
                        ? "bg-blue-50 text-blue-700 font-semibold"
                        : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
                    }`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
              <div className="pt-3 border-t border-gray-200 px-3 flex items-center justify-between">
                <span className="text-sm text-gray-700">
                  {user?.display_name} ({user?.role})
                </span>
                <button
                  type="button"
                  onClick={handleLogout}
                  className="text-sm font-medium text-red-600 hover:text-red-800"
                >
                  {t("auth.logout")}
                </button>
              </div>
            </nav>
          )}
        </div>
      </header>

      {/* Semantic main landmark */}
      <main id="main-content" tabIndex={-1} className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 focus:outline-none">
        <Outlet />
      </main>
    </div>
  );
}
