// API-sleutels voor de lees-API (/api/v1/stats), bv. voor een eigen dashboard (eigen account).
// Een sleutel kan enkel tellingen lezen: geen kluisitems, notities of links, en niets wijzigen.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import { formatDate, relative } from "../format";
import { Card, ErrorBox } from "./ui";

const BASE = "/api/v1/me/api-keys";

interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
}

export function ApiKeys({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me", "api-keys"], queryFn: () => api.get<ApiKey[]>(BASE) });
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("Dashboard");
  const [days, setDays] = useState("");
  const [token, setToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const refresh = () => void qc.invalidateQueries({ queryKey: ["me", "api-keys"] });
  const add = useMutation({
    mutationFn: () =>
      api.post<{ token: string }>(BASE, { name, expires_in_days: days ? Number(days) : null }),
    onSuccess: (data) => {
      setToken(data.token);
      setCopied(false);
      setAdding(false);
      refresh();
    },
  });
  const revoke = useMutation({ mutationFn: (id: string) => api.delete(`${BASE}/${id}`), onSuccess: refresh });

  const keys = q.data ?? [];
  const example = `curl -H "Authorization: Bearer ${token ?? "vxk_..."}" ${window.location.origin}/api/v1/stats`;
  return (
    <Card
      title="API-sleutels voor je dashboard"
      actions={
        !adding && (
          <button className="btn" onClick={() => setAdding(true)}>
            Sleutel aanmaken
          </button>
        )
      }
    >
      <ErrorBox error={q.error ?? add.error ?? revoke.error} />
      <p className="muted small">
        Met een sleutel leest een ander programma (bv. je dashboard) statistieken uit VaultX via{" "}
        <code className="mono">/api/v1/stats</code>: aantal kluisitems per soort, ingelogde toestellen, sessies,
        apps, links en logins van de laatste 24 uur.{" "}
        {isAdmin ? "Als beheerder krijg je de cijfers van heel VaultX." : "Je krijgt enkel je eigen cijfers."} Een
        sleutel kan niets wijzigen en geeft nooit wachtwoorden, notities of links zelf.
      </p>
      {token && (
        <div className="info-box">
          <p>
            <b>Kopieer deze sleutel nu.</b> VaultX bewaart enkel een hash; je ziet hem maar één keer.
          </p>
          <code className="mono recovery-code">{token}</code>{" "}
          <button
            className="btn btn-ghost small"
            onClick={() => void navigator.clipboard?.writeText(token).then(() => setCopied(true))}
          >
            {copied ? "Gekopieerd" : "Kopiëren"}
          </button>
          <p className="muted small">Proberen:</p>
          <code className="mono small">{example}</code>
        </div>
      )}
      {adding && (
        <form
          className="form"
          style={{ maxWidth: 520 }}
          onSubmit={(e) => {
            e.preventDefault();
            add.mutate();
          }}
        >
          <label className="field">
            <span>Naam (om de sleutel later te herkennen)</span>
            <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>Geldig</span>
            <select value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="">Altijd (tot je hem intrekt)</option>
              <option value="30">30 dagen</option>
              <option value="90">90 dagen</option>
              <option value="365">1 jaar</option>
            </select>
          </label>
          <div className="row">
            <button className="btn btn-primary" disabled={add.isPending}>
              Aanmaken
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setAdding(false)}>
              Annuleren
            </button>
          </div>
        </form>
      )}
      {keys.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Naam</th>
                <th>Sleutel</th>
                <th>Aangemaakt</th>
                <th>Verloopt</th>
                <th>Laatst gebruikt</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.id}>
                  <td>{k.name}</td>
                  <td className="mono">{k.prefix}…</td>
                  <td>{formatDate(k.created_at)}</td>
                  <td>{k.expires_at ? formatDate(k.expires_at) : "nooit"}</td>
                  <td>
                    {k.last_used_at ? `${relative(k.last_used_at)}${k.last_used_ip ? ` vanaf ${k.last_used_ip}` : ""}` : "nog niet"}
                  </td>
                  <td className="right">
                    <button
                      className="btn btn-ghost btn-danger-text"
                      disabled={revoke.isPending}
                      onClick={() => revoke.mutate(k.id)}
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
