export type OrgRole = "owner" | "admin" | "member";
export type TeamRole = "maintainer" | "member";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface MembershipBrief {
  id: string;
  organization_id: string;
  organization_slug: string;
  organization_name: string;
  team_id: string | null;
  team_slug: string | null;
  team_name: string | null;
  role: string;
  source: "manual" | "idp";
}

export interface User {
  id: string;
  email: string | null;
  username: string | null;
  display_name: string | null;
  is_active: boolean;
  email_verified?: boolean;
  is_admin?: boolean;
  idp_groups?: string[];
  last_login_at?: string | null;
  created_at?: string;
}

export interface UserDetail extends User {
  oidc_issuer: string;
  oidc_subject: string;
  memberships: MembershipBrief[];
}

export interface Me extends User {
  is_admin: boolean;
  memberships: MembershipBrief[];
  auth_method: string;
}

export interface Session {
  id: string;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  member_count: number;
  team_count: number;
  my_role: OrgRole | null;
}

export interface Team {
  id: string;
  organization_id: string;
  slug: string;
  name: string;
  description: string | null;
  created_at: string;
  member_count: number;
}

export interface Member {
  id: string;
  organization_id: string;
  team_id: string | null;
  role: string;
  source: "manual" | "idp";
  created_at: string;
  user: User;
}

export interface AuditEntry {
  id: number;
  occurred_at: string;
  organization_id: string | null;
  actor_user_id: string | null;
  actor_type: string;
  actor_label: string | null;
  action: string;
  outcome: "success" | "failure" | "denied";
  target_type: string | null;
  target_id: string | null;
  ip_address: string | null;
  user_agent: string | null;
  request_id: string | null;
  details: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface AuditPage {
  items: AuditEntry[];
  next_before_id: number | null;
}

export interface AuditVerify {
  organization_id: string | null;
  entries_checked: number;
  valid: boolean;
  broken_at_id: number | null;
  reason: string | null;
  head_hash: string | null;
}

export interface Dashboard {
  users_total: number;
  users_active: number;
  admins: number;
  organizations: number;
  teams: number;
  active_sessions: number;
  logins_24h: number;
  denied_24h: number;
  recent_events: AuditEntry[];
}
