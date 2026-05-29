import React from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { Search, Library, Users, LogOut, Settings as SettingsIcon, ScrollText, Shield } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import TrebleClef from "@/components/TrebleClef";

const ADMIN_EMAIL = "admin@scorelib.app";

export default function Header() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  if (!user) return null;

  const isAdmin = user.email?.toLowerCase() === ADMIN_EMAIL || user.is_admin;
  const navItems = [
    { to: "/", label: "Cerca", icon: Search, end: true, testId: "nav-search" },
    { to: "/library", label: "Libreria", icon: Library, testId: "nav-library" },
    { to: "/libraries", label: "Condivise", icon: Users, testId: "nav-shared" },
    { to: "/settings", label: "Impostazioni", icon: SettingsIcon, testId: "nav-settings" },
  ];
  if (isAdmin) {
    navItems.push(
      { to: "/logs", label: "Logs", icon: ScrollText, testId: "nav-logs" },
      { to: "/admin", label: "Admin", icon: Shield, testId: "nav-admin" },
    );
  }

  const desktopCls = ({ isActive }) =>
    `inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-sm transition-colors ${
      isActive ? "bg-canvas3 text-ink" : "text-[#525252] hover:text-ink"
    }`;
  const mobileCls = ({ isActive }) =>
    `flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 px-1 py-2 text-[11px] font-medium transition-colors ${
      isActive ? "text-ink" : "text-muted2"
    }`;

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <>
      <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-xl border-b border-rule" data-testid="app-header">
        <div className="max-w-7xl mx-auto px-4 md:px-12 py-3 flex items-center justify-between gap-2">
          <Link to="/" className="flex items-center gap-2.5 shrink-0" data-testid="brand-link">
            <TrebleClef size={26} />
            <span className="font-display font-bold text-lg tracking-tight">Scorelib</span>
          </Link>

          <nav className="hidden md:flex items-center gap-0.5">
            {navItems.map(({ to, label, icon: Icon, end, testId }) => (
              <NavLink key={to} to={to} end={end} className={desktopCls} data-testid={testId}>
                <Icon size={16} strokeWidth={1.75} /> <span>{label}</span>
              </NavLink>
            ))}
            <div className="flex items-center gap-2 ml-3 pl-3 border-l border-rule">
              <span className="text-mono text-xs text-muted2 max-w-[180px] truncate" data-testid="header-email">{user.email}</span>
              <button onClick={handleLogout} className="btn-ghost" data-testid="logout-btn" title="Logout">
                <LogOut size={16} strokeWidth={1.75} />
              </button>
            </div>
          </nav>

          <button onClick={handleLogout} className="btn-ghost md:hidden px-2" data-testid="logout-btn-mobile" title="Logout">
            <LogOut size={18} strokeWidth={1.75} />
          </button>
        </div>
      </header>

      <nav className="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-white/95 backdrop-blur-xl border-t border-rule grid grid-flow-col auto-cols-fr" data-testid="mobile-bottom-nav">
        {navItems.map(({ to, label, icon: Icon, end, testId }) => (
          <NavLink key={to} to={to} end={end} className={mobileCls} data-testid={`${testId}-mobile`}>
            <Icon size={18} strokeWidth={1.75} />
            <span className="truncate max-w-full">{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="md:hidden h-16" aria-hidden="true" />
    </>
  );
}
