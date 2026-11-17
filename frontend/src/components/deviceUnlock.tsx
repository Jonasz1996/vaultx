import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { formatDate, relative } from "../format";
import { Badge, Card, Empty, ErrorBox } from "./ui";

const BASE = "/api/v1/vault/device-unlock";

interface DeviceUnlock {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  last_used_ip: string | null;
}

interface DeviceUnlockStatus {
  server_enabled: boolean;
  user_enabled: boolean;
  require_mfa: boolean;
  max_auth_age_hours: number;
  eligible: boolean;
  reason: string | null;
  message: string | null;
  devices: DeviceUnlock[];
}

/** Mijn kluis: apparaten (VaultX Invuller) die de kluis openen met je Authentik-aanmelding. */
export function DeviceUnlockCard() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["device-unlock"], queryFn: () => api.get<DeviceUnlockStatus>(BASE) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["device-unlock"] });
  const revoke = useMutation({ mutationFn: (id: string) => api.delete(`${BASE}/${id}`), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.put(`${BASE}/settings`, { enabled }),
    onSuccess: refresh,
  });
  const s = status.data;
  if (!s) return <ErrorBox error={status.error} />;
  if (!s.server_enabled) return null;

  return (
    <Card
      title="Ontgrendelen met Authentik"
      actions={
        <label className="check">
          <input
            type="checkbox"
            checked={s.user_enabled}
            disabled={toggle.isPending}
            onChange={(e) => toggle.mutate(e.target.checked)}
          />
          Toestaan
        </label>
      }
    >
      <p className="muted small">
        Met de extensie VaultX Invuller gaat je kluis vanzelf open als je bij Authentik aangemeld bent
        {s.require_mfa ? " met een tweede stap (MFA)" : ""}, op een browser waar je één keer je master password gaf.
        Die browser bewaart de helft van de sleutel, VaultX de andere helft. Uitzetten trekt alle apparaten in; een
        nieuw master password ook.
      </p>
      {s.user_enabled && !s.eligible && s.message && <div className="info-box small">Nu niet mogelijk: {s.message}</div>}
      <ErrorBox error={revoke.error ?? toggle.error} />
      {!s.devices.length ? (
        <Empty>Nog geen apparaat.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Apparaat</th>
                <th>Gekoppeld</th>
                <th>Laatst geopend</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {s.devices.map((d) => (
                <tr key={d.id}>
                  <td>{d.name}</td>
                  <td>{formatDate(d.created_at)}</td>
                  <td>
                    {d.last_used_at ? (
                      <>
                        {relative(d.last_used_at)} {d.last_used_ip && <span className="muted small">({d.last_used_ip})</span>}
                      </>
                    ) : (
                      <Badge>nog niet</Badge>
                    )}
                  </td>
                  <td className="right">
                    <button
                      className="btn btn-ghost btn-danger-text"
                      onClick={() => revoke.mutate(d.id)}
                      disabled={revoke.isPending}
                    >
                      Intrekken
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
