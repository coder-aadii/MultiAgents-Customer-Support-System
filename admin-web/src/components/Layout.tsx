import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useTranslation } from "../hooks/useTranslation";

export default function Layout() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const navItems = [
    { path: "/inbox", label: t("nav.inbox") },
    { path: "/knowledge", label: t("nav.knowledge") },
    { path: "/settings/widget", label: t("nav.settings"), adminOnly: true },
    { path: "/audit", label: t("nav.audit"), adminOnly: true },
  ];

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center">
              <NavLink to="/inbox" className="text-2xl font-bold text-gray-900">
                MCSS
              </NavLink>
              <span className="ml-3 px-2 py-0.5 text-xs font-medium text-gray-500 bg-gray-100 rounded">
                {t("app.tagline")}
              </span>
            </div>
            <nav className="flex items-center space-x-4">
              {navItems.map((item) => (
                !item.adminOnly || user?.role === "admin" ? (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    className={({ isActive }) =>
                      `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                        isActive
                          ? "bg-blue-50 text-blue-700"
                          : "text-gray-600 hover:text-gray-900 hover:bg-gray-50"
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                ) : null
              ))}
              <div className="flex items-center space-x-3 pl-4 border-l border-gray-200">
                <span className="text-sm text-gray-700">
                  {user?.display_name} ({user?.role})
                </span>
                <button
                  onClick={handleLogout}
                  className="text-sm text-gray-600 hover:text-gray-900"
                >
                  {t("auth.logout")}
                </button>
              </div>
            </nav>
          </div>
        </div>
      </header>
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <Outlet />
      </main>
    </div>
  );
}