import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Page, User } from "../api/types";
import { Badge, Card, ErrorBox, Loading, PageHeader } from "../components/ui";
import { relative, userLabel } from "../format";

const PAGE = 25;

export function UsersPage() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const { data, error, isLoading } = useQuery({
    queryKey: ["users", q, offset],
    queryFn: () => api.get<Page<User>>("/api/v1/users", { q, limit: PAGE, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader
        title="Gebruikers"
        subtitle="Gebruikers worden aangemaakt bij hun eerste login via Authentik. Beheerdersrechten volgen de Authentik-groepen."
      />
      <Card
        actions={
          <input
            className="search"
            placeholder="Zoeken…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        }
      >
        <ErrorBox error={error} />
        {isLoading ? (
          <Loading />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Naam</th>
                  <th>E-mail</th>
                  <th>Status</th>
                  <th>Authentik-groepen</th>
                  <th>Laatste login</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <Link to={`/users/${u.id}`}>{userLabel(u)}</Link>
                      {u.is_admin && <Badge tone="info">admin</Badge>}
                    </td>
                    <td>{u.email}</td>
                    <td>{u.is_active ? <Badge tone="good">actief</Badge> : <Badge tone="bad">gedeactiveerd</Badge>}</td>
                    <td className="small">{u.idp_groups?.join(", ")}</td>
                    <td className="nowrap">{relative(u.last_login_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > PAGE && (
          <div className="pager">
            <button className="btn" disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
              Vorige
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} van {data.total}
            </span>
            <button className="btn" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>
              Volgende
            </button>
          </div>
        )}
      </Card>
    </>
  );
}
