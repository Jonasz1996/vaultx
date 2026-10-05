import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Page, User } from "../api/types";
import { userLabel } from "../format";

/** Zoekveld om een bestaande gebruiker te kiezen (gebruikers ontstaan bij hun eerste login). */
export function UserPicker({ onPick, exclude }: { onPick: (u: User) => void; exclude: Set<string> }) {
  const [q, setQ] = useState("");
  const { data, isFetching } = useQuery({
    queryKey: ["users", "pick", q],
    queryFn: () => api.get<Page<User>>("/api/v1/users", { q, limit: 8 }),
    enabled: q.trim().length >= 2,
  });
  const results = (data?.items ?? []).filter((u) => !exclude.has(u.id));
  return (
    <div className="picker">
      <input
        autoFocus
        placeholder="Zoek op naam, e-mail of gebruikersnaam…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {q.trim().length >= 2 && (
        <ul className="picker-list">
          {isFetching && <li className="muted">Zoeken…</li>}
          {!isFetching && results.length === 0 && (
            <li className="muted">Geen resultaten. Gebruikers verschijnen na hun eerste login via Authentik.</li>
          )}
          {results.map((u) => (
            <li key={u.id}>
              <button type="button" onClick={() => onPick(u)}>
                <strong>{userLabel(u)}</strong>
                <span className="muted">{u.email}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
