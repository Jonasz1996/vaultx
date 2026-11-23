import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
export function useMe() {
    return useQuery({ queryKey: ["me"], queryFn: () => api.get("/api/v1/me"), retry: false });
}
/** Rol van de ingelogde gebruiker in een organisatie (instantiebeheerders mogen alles). */
export function canManageOrg(me, orgId) {
    if (!me)
        return false;
    if (me.is_admin)
        return true;
    return me.memberships.some((m) => m.organization_id === orgId && m.team_id === null && (m.role === "owner" || m.role === "admin"));
}
export function isOrgOwner(me, orgId) {
    if (!me)
        return false;
    if (me.is_admin)
        return true;
    return me.memberships.some((m) => m.organization_id === orgId && m.team_id === null && m.role === "owner");
}
export function canManageTeam(me, orgId, teamId) {
    return (canManageOrg(me, orgId) ||
        !!me?.memberships.some((m) => m.team_id === teamId && m.role === "maintainer"));
}
