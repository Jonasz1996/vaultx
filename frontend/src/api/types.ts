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

// ---------------------------------------------------------------- NPM en catalogus

export type AuthMethod =
  | "forward_auth"
  | "oidc"
  | "saml"
  | "header"
  | "access_list"
  | "app"
  | "none"
  | "unknown";

export type AppStatus = "protected" | "restricted" | "unprotected" | "unknown" | "offline" | "removed";

export interface DetectionWarning {
  code: string;
  message: string;
}

export interface NpmConnection {
  id: string;
  organization_id: string;
  name: string;
  base_url: string;
  identity: string;
  verify_tls: boolean;
  enabled: boolean;
  npm_version: string | null;
  last_sync_at: string | null;
  last_sync_status: "ok" | "error" | null;
  last_sync_error: string | null;
  write_enabled: boolean;
  authentik_outpost_url: string | null;
  probe_host: string | null;
  probe_http_port: number;
  probe_https_port: number;
  authentik_outpost_pk: string | null;
  created_at: string;
  updated_at: string;
  host_count: number;
}

export interface AuthentikOutpost {
  pk: string;
  name: string;
  managed: string | null;
  authentik_host: string | null;
  provider_count: number;
}

export interface AuthentikOutposts {
  configured: boolean;
  api_url: string;
  outposts: AuthentikOutpost[];
  error: string | null;
}

// Wie de Authentik-applicatie mag openen: "all", "organization" of "team:<slug>".
export type ProtectionAccess = string;

export interface AuthentikState {
  domain: string;
  external_host: string;
  access: string;
  outpost_pk: string;
  outpost_name: string | null;
  outpost_assigned: boolean;
  provider_pk: number | null;
  provider_name: string | null;
  provider_created: boolean;
  application_slug: string | null;
  application_name: string | null;
  application_created: boolean;
  groups: string[];
}

export interface AuthentikChange {
  plan?: { steps: string[]; groups: string[]; access: string };
  state?: AuthentikState;
  remove?: AuthentikState;
  undone?: boolean;
  undo_error?: string;
  removed?: boolean;
  cleanup_error?: string;
}

export interface SyncResult {
  connection_id: string;
  npm_version: string | null;
  hosts_total: number;
  hosts_new: number;
  hosts_updated: number;
  hosts_removed: number;
  applications_created: number;
  applications_updated: number;
}

export interface DiscoveredHost {
  id: string;
  connection_id: string;
  npm_id: number;
  domain_names: string[];
  forward_scheme: string;
  forward_host: string;
  forward_port: number;
  enabled: boolean;
  nginx_online: boolean;
  ssl: boolean;
  ssl_forced: boolean;
  access_list: string | null;
  forward_auth: boolean;
  detected_app_type: string | null;
  detected_auth: AuthMethod;
  labels: Record<string, string>;
  warnings: DetectionWarning[];
  ignored: boolean;
  vaultx_managed: boolean;
  first_seen_at: string;
  last_seen_at: string;
  removed_at: string | null;
  application: { id: string; name: string } | null;
}

export type ProtectionAction = "protect" | "unprotect";

export interface ProtectionCheck {
  code: string;
  level: "block" | "warn" | "info";
  message: string;
}

export interface NpmLocation {
  path: string;
  forward_scheme: string;
  forward_host: string;
  forward_port: number;
  forward_path?: string | null;
  advanced_config?: string | null;
  [key: string]: unknown;
}

export interface HostConfig {
  advanced_config: string;
  locations: NpmLocation[];
}

export interface ProtectionPlan {
  action: ProtectionAction;
  npm_id: number;
  domain: string;
  modified_on: string | null;
  can_apply: boolean;
  checks: ProtectionCheck[];
  steps: string[];
  before: HostConfig;
  after: HostConfig;
  probe_url: string | null;
  authentik: {
    external_host?: string | null;
    outpost?: string | null;
    groups?: string[];
    access_label?: string;
    remove?: AuthentikState;
  } | null;
}

export interface ProbeResult {
  url: string;
  status: number | null;
  location: string | null;
  error: string | null;
  summary: string;
}

export type NpmChangeStatus = "running" | "applied" | "rolled_back" | "rollback_failed" | "refused" | "interrupted";

export interface NpmChange {
  id: string;
  connection_id: string;
  host_id: string | null;
  npm_id: number;
  domain: string;
  action: ProtectionAction;
  status: NpmChangeStatus;
  verified: boolean;
  actor_label: string | null;
  message: string | null;
  nginx_error: string | null;
  before: Partial<HostConfig>;
  after: Partial<HostConfig> | null;
  probe_before: ProbeResult | null;
  probe_after: ProbeResult | null;
  authentik: AuthentikChange | null;
  created_at: string;
  finished_at: string | null;
}

export interface ApplicationHost {
  id: string;
  connection_id: string;
  connection_name: string;
  domain_names: string[];
  forward: string;
  enabled: boolean;
  nginx_online: boolean;
  ssl: boolean;
  forward_auth: boolean;
  warnings: DetectionWarning[];
  removed_at: string | null;
}

export interface Application {
  id: string;
  organization_id: string;
  name: string;
  app_type: string | null;
  url: string | null;
  description: string | null;
  auth_method: AuthMethod;
  status: AppStatus;
  tags: string[];
  source: "npm" | "manual";
  auto_update: boolean;
  hosts: ApplicationHost[];
  warning_count: number;
  auto_login: boolean;
  created_at: string;
  updated_at: string;
}

// Fase 5: automatische login via Authentik (de app wordt een OIDC-client van Authentik).
export interface AppLogin {
  id: string;
  application_id: string;
  app_url: string;
  redirect_uris: string[];
  access: string;
  groups: string[];
  provider_pk: number;
  provider_name: string;
  provider_created: boolean;
  application_slug: string | null;
  application_name: string | null;
  application_created: boolean;
  client_id: string;
  cleanup_error: string | null;
  created_at: string;
}

export interface AppLoginState {
  configured: boolean;
  authentik_url: string;
  suggested_app_url: string | null;
  login: AppLogin | null;
}

export interface AppLoginInput {
  redirect_uris: string[];
  access: string;
  app_url: string | null;
}

export interface AppLoginPlan {
  app_url: string | null;
  redirect_uris: string[];
  access: string;
  can_apply: boolean;
  checks: ProtectionCheck[];
  steps: string[];
  groups: string[];
}

export interface AppLoginConfig {
  client_id: string;
  client_secret: string;
  issuer: string;
  discovery_url: string;
  authorization_url: string;
  token_url: string;
  userinfo_url: string;
  end_session_url: string;
  scopes: string[];
  redirect_uris: string[];
  text: string;
}

export interface VaultDevice {
  id: string;
  name: string;
  type: number;
  type_name: string;
  created_at: string;
  last_seen_at: string;
  revoked_at: string | null;
}

export interface VaultStatus {
  enrolled: boolean;
  email: string | null;
  blocked_reason: string | null;
  server_url: string;
  kdf: number | null;
  kdf_iterations: number | null;
  kdf_memory: number | null;
  kdf_parallelism: number | null;
  enrolled_at: string | null;
  revision_date: string | null;
  item_count: number;
  trash_count: number;
  folder_count: number;
  devices: VaultDevice[];
}
