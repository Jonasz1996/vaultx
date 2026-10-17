import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
import type { AuditPage, AuditVerify, Organization, Page } from "../api/types";
import { AuditTable } from "../components/AuditTable";
import { Badge, Card, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";

const LIMIT = 50;

export function AuditPageView() {
  const { data: me } = useMe();
  const [params, setParams] = useSearchParams();
  const filters = {
    organization_id: params.get("organization_id") ?? "",
    instance_only: params.get("instance_only") === "true",
    action: params.get("action") ?? "",
    outcome: params.get("outcome") ?? "",
  };
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  const orgs = useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<Page<Organization>>("/api/v1/organizations", { limit: 200 }),
  });
  const feed = useInfiniteQuery({
    queryKey: ["audit", "feed", filters],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      api.get<AuditPage>("/api/v1/audit", {
        limit: LIMIT,
        before_id: pageParam,
        organization_id: filters.organization_id || undefined,
        instance_only: filters.instance_only || undefined,
        action: filters.action || undefined,
        outcome: filters.outcome || undefined,
      }),
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
  const verify = useMutation({
    mutationFn: () =>
      api.get<AuditVerify>("/api/v1/audit/verify", { organization_id: filters.organization_id || undefined }),
  });
  const items = feed.data?.pages.flatMap((p) => p.items) ?? [];
  const canVerify = me?.is_admin || !!filters.organization_id;

  return (
    <>
      <PageHeader
        title="Audit"
        subtitle="Append-only log met een SHA-256-hashketen per organisatie en één voor de instantie."
      />
      <Card
        actions={
          <div className="row wrap">
            <select
              value={filters.instance_only ? "__instance" : filters.organization_id}
              onChange={(e) => {
                const v = e.target.value;
                const next = new URLSearchParams(params);
                next.delete("organization_id");
                next.delete("instance_only");
                if (v === "__instance") next.set("instance_only", "true");
                else if (v) next.set("organization_id", v);
                setParams(next, { replace: true });
                verify.reset();
              }}
            >
              <option value="">Alle ketens</option>
              {me?.is_admin && <option value="__instance">Instantie (logins, gebruikers)</option>}
              {orgs.data?.items.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
            <input
              placeholder="Actie-prefix, bv. auth."
              value={filters.action}
              onChange={(e) => set("action", e.target.value)}
            />
            <select value={filters.outcome} onChange={(e) => set("outcome", e.target.value)}>
              <option value="">Alle resultaten</option>
              <option value="success">success</option>
              <option value="failure">failure</option>
              <option value="denied">denied</option>
            </select>
            {canVerify && (
              <button className="btn" onClick={() => verify.mutate()} disabled={verify.isPending}>
                Keten controleren
              </button>
            )}
          </div>
        }
      >
        {verify.data && (
          <div className={verify.data.valid ? "info-box" : "error-box"}>
            {verify.data.valid ? (
              <>
                <Badge tone="good">intact</Badge> {verify.data.entries_checked} regels gecontroleerd in de{" "}
                {verify.data.organization_id ? "organisatieketen" : "instantieketen"}.
              </>
            ) : (
              <>
                <Badge tone="bad">gebroken</Badge> bij regel #{verify.data.broken_at_id}: {verify.data.reason}
              </>
            )}
          </div>
        )}
        <ErrorBox error={feed.error ?? verify.error} />
        {feed.isLoading ? (
          <Loading />
        ) : items.length === 0 ? (
          <Empty>Geen gebeurtenissen voor deze filter.</Empty>
        ) : (
          <AuditTable items={items} />
        )}
        {feed.hasNextPage && (
          <div className="pager">
            <button className="btn" onClick={() => void feed.fetchNextPage()} disabled={feed.isFetchingNextPage}>
              Meer laden
            </button>
          </div>
        )}
      </Card>
    </>
  );
}
