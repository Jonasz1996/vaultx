import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { api } from "../api/client";
import type { Page, User, UserDetail } from "../api/types";
import { CreateAccountForm, type NewAccount } from "../components/accounts";
import { Badge, Card, ErrorBox, Loading, Modal, PageHeader } from "../components/ui";
import { relative, userLabel } from "../format";

const PAGE = 25;

export function UsersPage() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const methods = useQuery({
    queryKey: ["auth-methods"],
    queryFn: () => api.get<{ password: boolean }>("/auth/methods"),
  });
  const create = useMutation({
    mutationFn: (v: NewAccount) => api.post<UserDetail>("/api/v1/users", v),
    onSuccess: (u) => {
      void qc.invalidateQueries({ queryKey: ["users"] });
      setCreating(false);
      navigate(`/users/${u.id}`);
    },
  });
  const { data, error, isLoading } = useQuery({
    queryKey: ["users", q, offset],
    queryFn: () => api.get<Page<User>>("/api/v1/users", { q, limit: PAGE, offset }),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader
        title="Gebruikers"
        subtitle="Authentik-gebruikers verschijnen bij hun eerste login. Gewone accounts met een VaultX-wachtwoord maak je hier zelf aan."
        actions={
          methods.data?.password && (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              Account aanmaken
            </button>
          )
        }
      />
      <Modal title="Gewoon account aanmaken" open={creating} onClose={() => setCreating(false)}>
        <CreateAccountForm onSubmit={(v) => create.mutate(v)} error={create.error} pending={create.isPending} />
      </Modal>
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
                  <th>Inloggen</th>
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
                    <td className="nowrap">
                      {u.has_authentik && <Badge tone="info">Authentik</Badge>} {u.has_password && <Badge>wachtwoord</Badge>}
                    </td>
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
