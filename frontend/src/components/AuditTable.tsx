import { Link } from "react-router";
import type { AuditEntry } from "../api/types";
import { formatDate } from "../format";
import { Badge } from "./ui";

const outcomeTone = { success: "good", failure: "warn", denied: "bad" } as const;

export function AuditTable({ items, compact = false }: { items: AuditEntry[]; compact?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Tijd</th>
            <th>Actie</th>
            <th>Actor</th>
            <th>Resultaat</th>
            {!compact && <th>Doel</th>}
            {!compact && <th>IP</th>}
            {!compact && <th>Details</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id}>
              <td className="nowrap">{formatDate(e.occurred_at)}</td>
              <td>
                <code>{e.action}</code>
              </td>
              <td>
                {e.actor_user_id ? (
                  <Link to={`/users/${e.actor_user_id}`}>{e.actor_label ?? e.actor_user_id}</Link>
                ) : (
                  <span className="muted">
                    {e.actor_type}
                    {e.actor_label ? `: ${e.actor_label}` : ""}
                  </span>
                )}
              </td>
              <td>
                <Badge tone={outcomeTone[e.outcome]}>{e.outcome}</Badge>
              </td>
              {!compact && (
                <td className="small">
                  {e.target_type && (
                    <>
                      {e.target_type} <span className="muted mono">{e.target_id?.slice(0, 8)}</span>
                    </>
                  )}
                </td>
              )}
              {!compact && <td className="small mono">{e.ip_address}</td>}
              {!compact && (
                <td>
                  {Object.keys(e.details).length > 0 && (
                    <details>
                      <summary>bekijk</summary>
                      <pre>{JSON.stringify(e.details, null, 2)}</pre>
                      <div className="muted small mono">hash {e.hash.slice(0, 16)}…</div>
                    </details>
                  )}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
