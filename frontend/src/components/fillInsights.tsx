import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router";
import { api } from "../api/client";
import { relative } from "../format";
import { loadWebVault, SYNC_KEY } from "../pages/VaultItems";
import type { ItemView } from "../vault/items";
import { useWebVault } from "../vault/webSession";
import { Badge, Card, Empty, ErrorBox, Stat } from "./ui";

interface FillItem {
  item_id: string;
  host: string;
  fills: number;
  failures: number;
  last_fill_at: string | null;
  last_failure_at: string | null;
}

interface FillInsights {
  days: number;
  fills: number;
  failures: number;
  items: FillItem[];
}

/** Zoals de extensie: een adres zonder schema geldt als https. */
function scheme(uri: string): string | null {
  const u = uri.trim();
  if (!u) return null;
  const m = /^([a-z][a-z0-9+.-]*):\/\//i.exec(u);
  return m ? m[1].toLowerCase() : "https";
}

/** Login-items die de extensie nooit (veilig) kan invullen: zonder adres, of enkel via http. */
export function uriProblems(items: ItemView[]) {
  const logins = items.filter((i) => i.type === 1 && !i.deletedDate);
  const noUri: ItemView[] = [];
  const httpOnly: ItemView[] = [];
  for (const i of logins) {
    const schemes = i.login.uris.map((u) => scheme(u.uri)).filter((s): s is string => s !== null);
    const web = schemes.filter((s) => s === "http" || s === "https");
    if (!web.length) noUri.push(i);
    else if (web.every((s) => s === "http")) httpOnly.push(i);
  }
  return { noUri, httpOnly };
}

/** Mijn kluis: wat VaultX Invuller invulde, mislukte logins, en items zonder (veilig) adres. */
export function FillInsightCard() {
  const insights = useQuery({
    queryKey: ["fill-insights"],
    queryFn: () => api.get<FillInsights>("/api/v1/vault/fill-insights"),
  });
  const { key, request } = useWebVault();
  // Namen en adressen staan enkel versleuteld op de server: die toont enkel een open webkluis.
  const vault = useQuery({
    queryKey: SYNC_KEY,
    queryFn: () => loadWebVault(request, key!),
    enabled: Boolean(key),
    staleTime: 30_000,
  });
  const d = insights.data;
  if (!d) return <ErrorBox error={insights.error} />;
  const names = new Map((vault.data?.items ?? []).map((i) => [i.id, i.name]));
  const problems = vault.data ? uriProblems(vault.data.items) : null;
  const failed = d.items.filter((r) => r.failures > 0);
  const name = (id: string) => names.get(id) ?? <span className="muted small mono">{id.slice(0, 8)}…</span>;

  return (
    <Card title="VaultX Invuller">
      <div className="stats">
        <Stat label={`invullingen (${d.days} dagen)`} value={d.fills} />
        <Stat label="mislukte logins" value={d.failures} />
        <Stat label="items ingevuld" value={new Set(d.items.filter((r) => r.fills).map((r) => r.item_id)).size} />
      </div>
      {!key && (
        <p className="muted small">
          VaultX ziet enkel het id van een item en de host: de namen en adressen zijn versleuteld.{" "}
          <NavLink to="/vault">Open de webkluis</NavLink> om hier de namen en items zonder adres te zien.
        </p>
      )}
      {failed.length > 0 && (
        <div className="info-box small" role="alert">
          <strong>Login mislukt na invullen</strong>: wachtwoord klopt niet meer?{" "}
          {failed.map((r, n) => (
            <span key={`${r.item_id}-${r.host}`}>
              {n > 0 && ", "}
              {name(r.item_id)} op {r.host}
            </span>
          ))}
          . VaultX Invuller probeert het daar niet opnieuw tot je het item aanpast.
        </div>
      )}
      {!d.items.length ? (
        <Empty>De extensie vulde de laatste {d.days} dagen nog niets in.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Item</th>
                <th>Host</th>
                <th>Ingevuld</th>
                <th>Laatst</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((r) => (
                <tr key={`${r.item_id}-${r.host}`}>
                  <td>{name(r.item_id)}</td>
                  <td className="mono small">{r.host}</td>
                  <td>
                    {r.fills}× {r.failures > 0 && <Badge tone="bad">{r.failures}× mislukt</Badge>}
                  </td>
                  <td>{r.last_fill_at ? relative(r.last_fill_at) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {problems && (problems.noUri.length > 0 || problems.httpOnly.length > 0) && (
        <>
          <h3>Aandacht nodig</h3>
          {problems.noUri.length > 0 && (
            <p className="small">
              <Badge tone="warn">zonder adres</Badge> {problems.noUri.map((i) => i.name).join(", ")}
            </p>
          )}
          {problems.httpOnly.length > 0 && (
            <p className="small">
              <Badge tone="warn">enkel http</Badge> {problems.httpOnly.map((i) => i.name).join(", ")}{" "}
              <span className="muted">(het wachtwoord gaat dan onversleuteld over het netwerk; zet er https:// bij als de site dat kan)</span>
            </p>
          )}
        </>
      )}
      {problems && !problems.noUri.length && !problems.httpOnly.length && (
        <p className="muted small">Elk login-item heeft een adres met https.</p>
      )}
    </Card>
  );
}
