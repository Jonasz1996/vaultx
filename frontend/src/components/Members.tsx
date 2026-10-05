import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Member } from "../api/types";
import { roleLabels, userLabel } from "../format";
import { UserPicker } from "./UserPicker";
import { Card, Empty, ErrorBox, Loading, Modal, SourceBadge } from "./ui";

/** Ledenbeheer voor een organisatie (teamId undefined) of een team. */
export function Members({
  basePath,
  roles,
  canManage,
  canGrantOwner = false,
  title = "Leden",
}: {
  basePath: string;
  roles: string[];
  canManage: boolean;
  canGrantOwner?: boolean;
  title?: string;
}) {
  const qc = useQueryClient();
  const key = ["members", basePath];
  const [adding, setAdding] = useState(false);
  const [role, setRole] = useState(roles[roles.length - 1]);
  const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => api.get<Member[]>(`${basePath}/members`) });
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: key });
    void qc.invalidateQueries({ queryKey: ["teams"] });
    void qc.invalidateQueries({ queryKey: ["org"] });
  };
  const add = useMutation({
    mutationFn: (userId: string) => api.post(`${basePath}/members`, { user_id: userId, role }),
    onSuccess: () => {
      setAdding(false);
      invalidate();
    },
  });
  const update = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api.patch(`${basePath}/members/${id}`, { role }),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`${basePath}/members/${id}`),
    onSuccess: invalidate,
  });
  const grantable = roles.filter((r) => r !== "owner" || canGrantOwner);

  return (
    <Card
      title={`${title}${data ? ` (${data.length})` : ""}`}
      actions={
        canManage && (
          <button className="btn btn-primary" onClick={() => setAdding(true)}>
            Lid toevoegen
          </button>
        )
      }
    >
      <ErrorBox error={error ?? update.error ?? remove.error} />
      {isLoading ? (
        <Loading />
      ) : !data?.length ? (
        <Empty>Nog geen leden.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Gebruiker</th>
                <th>Rol</th>
                <th>Bron</th>
                {canManage && <th />}
              </tr>
            </thead>
            <tbody>
              {data.map((m) => {
                const editable = canManage && m.source === "manual" && (m.role !== "owner" || canGrantOwner);
                return (
                  <tr key={m.id}>
                    <td>
                      <Link to={`/users/${m.user.id}`}>{userLabel(m.user)}</Link>
                      <div className="muted small">{m.user.email}</div>
                    </td>
                    <td>
                      {editable ? (
                        <select
                          value={m.role}
                          onChange={(e) => update.mutate({ id: m.id, role: e.target.value })}
                          aria-label="Rol"
                        >
                          {grantable.map((r) => (
                            <option key={r} value={r}>
                              {roleLabels[r]}
                            </option>
                          ))}
                        </select>
                      ) : (
                        roleLabels[m.role] ?? m.role
                      )}
                    </td>
                    <td>
                      <SourceBadge source={m.source} />
                    </td>
                    {canManage && (
                      <td className="right">
                        {editable && (
                          <button
                            className="btn btn-ghost btn-danger-text"
                            onClick={() => {
                              if (confirm(`${userLabel(m.user)} verwijderen?`)) remove.mutate(m.id);
                            }}
                          >
                            Verwijderen
                          </button>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Modal title="Lid toevoegen" open={adding} onClose={() => setAdding(false)}>
        <label className="field">
          <span>Rol</span>
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {grantable.map((r) => (
              <option key={r} value={r}>
                {roleLabels[r]}
              </option>
            ))}
          </select>
        </label>
        <UserPicker onPick={(u) => add.mutate(u.id)} exclude={new Set(data?.map((m) => m.user.id))} />
        <ErrorBox error={add.error} />
      </Modal>
    </Card>
  );
}
