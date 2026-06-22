import { type ReactNode } from "react";
import { NavLink } from "react-router-dom";

const NAV = [
  { to: "/", label: "Dashboard", icon: "◈", end: true },
  { to: "/search", label: "Song Search", icon: "⌕", end: false },
  { to: "/valuation", label: "Valuation", icon: "▲", end: false },
  { to: "/health", label: "Data Health", icon: "✚", end: false },
];

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">♪</div>
          <div>
            <div className="brand-name">Catalog</div>
            <div className="brand-sub">Valuation Tool</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <span className="nav-ico">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}
