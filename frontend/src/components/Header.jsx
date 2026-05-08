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
  const cls = ({ isActive }) =>
    `inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-sm transition-colors ${
      isActive ? "bg-canvas3 text-ink" : "text-[#525252] hover:text-ink"
    }`;
  return (
    <header className="sticky top-0 z-40 bg-white/90 backdrop-blur-xl border-b border-rule" data-testid="app-header">
      <div className="max-w-7xl mx-auto px-4 md:px-12 py-3 flex items-center justify-between gap-2">
        <Link to="/" className="flex items-center gap-2.5 shrink-0" data-testid="brand-link">
          <TrebleClef size={26} />
          <span className="font-display font-bold text-lg tracking-tight">Scorelib</span>
        </Link>
        <nav className="flex items-center gap-0.5 overflow-x-auto">
          <NavLink to="/" end className={cls} data-testid="nav-search">
            <Search size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Cerca</span>
          </NavLink>
          <NavLink to="/library" className={cls} data-testid="nav-library">
            <Library size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Libreria</span>
          </NavLink>
          <NavLink to="/libraries" className={cls} data-testid="nav-shared">
            <Users size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Condivise</span>
          </NavLink>
          <NavLink to="/settings" className={cls} data-testid="nav-settings">
            <SettingsIcon size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Impostazioni</span>
          </NavLink>
          <NavLink to="/logs" className={cls} data-testid="nav-logs">
            <ScrollText size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Logs</span>
          </NavLink>
          {isAdmin && (
            <NavLink to="/admin" className={cls} data-testid="nav-admin">
              <Shield size={16} strokeWidth={1.75} /> <span className="hidden sm:inline">Admin</span>
            </NavLink>
          )}
          <div className="hidden md:flex items-center gap-2 ml-3 pl-3 border-l border-rule">
            <span className="text-mono text-xs text-muted2 max-w-[180px] truncate" data-testid="header-email">{user.email}</span>
            <button onClick={() => { logout(); navigate("/login"); }} className="btn-ghost" data-testid="logout-btn" title="Logout">
              <LogOut size={16} strokeWidth={1.75} />
            </button>
          </div>
        </nav>
      </div>
    </header>
  );
}
