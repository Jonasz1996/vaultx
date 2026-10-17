import { useEffect, useRef, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

type Tone = "neutral" | "good" | "warn" | "bad" | "info";

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : String(error);
  return <div className="error-box" role="alert">{msg}</div>;
}

export function Loading() {
  return <div className="loading">Laden…</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Modal({
  title,
  open,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={wide ? "modal modal-wide" : "modal"} onClose={onClose}>
      <div className="modal-head">
        <h2>{title}</h2>
        <button className="btn-icon" onClick={onClose} aria-label="Sluiten">
          ×
        </button>
      </div>
      {open && children}
    </dialog>
  );
}

export function SourceBadge({ source }: { source: "manual" | "idp" }) {
  return source === "idp" ? (
    <Badge tone="info" title="Beheerd via een Authentik-groep">
      Authentik
    </Badge>
  ) : (
    <Badge>Manueel</Badge>
  );
}
