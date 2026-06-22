import { type ReactNode } from "react";

export function PageHead({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
    </div>
  );
}

export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  );
}

export function StatCard({
  label,
  value,
  sub,
  tone = "ok",
}: {
  label: string;
  value: ReactNode;
  sub?: string;
  tone?: "ok" | "warn" | "danger";
}) {
  return (
    <div className={`card stat ${tone === "ok" ? "" : tone}`}>
      <div className="stat-label">
        <span className="dot" />
        {label}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function SampleBanner({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <div className="banner">
      <span>⚠</span>
      <span>
        Showing <strong>synthetic sample data</strong> — run the scraper and
        regenerate to load real Spotify Global 200 figures.
      </span>
    </div>
  );
}

export function Loading({ what = "data" }: { what?: string }) {
  return <div className="loading">Loading {what}…</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
