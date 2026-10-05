import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { Me } from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/me"), retry: false });
}

/** Rol van de ingelogde gebruiker in een organisatie (instantiebeheerders mogen alles). */
export function canManageOrg(me: Me | undefined, orgId: string): boolean {
  if (!me) return false;
  if (me.is_admin) return true;
  return me.memberships.some(
    (m) => m.organization_id === orgId && m.team_id === null && (m.role === "owner" || m.role === "admin"),
  );
}

export function isOrgOwner(me: Me | undefined, orgId: string): boolean {
  if (!me) return false;
  if (me.is_admin) return true;
  return me.memberships.some((m) => m.organization_id === orgId && m.team_id === null && m.role === "owner");
}

export function canManageTeam(me: Me | undefined, orgId: string, teamId: string): boolean {
  return (
    canManageOrg(me, orgId) ||
    !!me?.memberships.some((m) => m.team_id === teamId && m.role === "maintainer")
  );
}
