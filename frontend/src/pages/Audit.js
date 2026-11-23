import { Fragment as _Fragment, jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../api/client";
import { useMe } from "../api/hooks";
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
    const set = (k, v) => {
        const next = new URLSearchParams(params);
        if (v)
            next.set(k, v);
        else
            next.delete(k);
        setParams(next, { replace: true });
    };
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => api.get("/api/v1/organizations", { limit: 200 }),
    });
    const feed = useInfiniteQuery({
        queryKey: ["audit", "feed", filters],
        initialPageParam: undefined,
        queryFn: ({ pageParam }) => api.get("/api/v1/audit", {
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
        mutationFn: () => api.get("/api/v1/audit/verify", { organization_id: filters.organization_id || undefined }),
    });
    const items = feed.data?.pages.flatMap((p) => p.items) ?? [];
    const canVerify = me?.is_admin || !!filters.organization_id;
    return (_jsxs(_Fragment, { children: [_jsx(PageHeader, { title: "Audit", subtitle: "Append-only log met een SHA-256-hashketen per organisatie en \u00E9\u00E9n voor de instantie." }), _jsxs(Card, { actions: _jsxs("div", { className: "row wrap", children: [_jsxs("select", { value: filters.instance_only ? "__instance" : filters.organization_id, onChange: (e) => {
                                const v = e.target.value;
                                const next = new URLSearchParams(params);
                                next.delete("organization_id");
                                next.delete("instance_only");
                                if (v === "__instance")
                                    next.set("instance_only", "true");
                                else if (v)
                                    next.set("organization_id", v);
                                setParams(next, { replace: true });
                                verify.reset();
                            }, children: [_jsx("option", { value: "", children: "Alle ketens" }), me?.is_admin && _jsx("option", { value: "__instance", children: "Instantie (logins, gebruikers)" }), orgs.data?.items.map((o) => (_jsx("option", { value: o.id, children: o.name }, o.id)))] }), _jsx("input", { placeholder: "Actie-prefix, bv. auth.", value: filters.action, onChange: (e) => set("action", e.target.value) }), _jsxs("select", { value: filters.outcome, onChange: (e) => set("outcome", e.target.value), children: [_jsx("option", { value: "", children: "Alle resultaten" }), _jsx("option", { value: "success", children: "success" }), _jsx("option", { value: "failure", children: "failure" }), _jsx("option", { value: "denied", children: "denied" })] }), canVerify && (_jsx("button", { className: "btn", onClick: () => verify.mutate(), disabled: verify.isPending, children: "Keten controleren" }))] }), children: [verify.data && (_jsx("div", { className: verify.data.valid ? "info-box" : "error-box", children: verify.data.valid ? (_jsxs(_Fragment, { children: [_jsx(Badge, { tone: "good", children: "intact" }), " ", verify.data.entries_checked, " regels gecontroleerd in de", " ", verify.data.organization_id ? "organisatieketen" : "instantieketen", "."] })) : (_jsxs(_Fragment, { children: [_jsx(Badge, { tone: "bad", children: "gebroken" }), " bij regel #", verify.data.broken_at_id, ": ", verify.data.reason] })) })), _jsx(ErrorBox, { error: feed.error ?? verify.error }), feed.isLoading ? (_jsx(Loading, {})) : items.length === 0 ? (_jsx(Empty, { children: "Geen gebeurtenissen voor deze filter." })) : (_jsx(AuditTable, { items: items })), feed.hasNextPage && (_jsx("div", { className: "pager", children: _jsx("button", { className: "btn", onClick: () => void feed.fetchNextPage(), disabled: feed.isFetchingNextPage, children: "Meer laden" }) }))] })] }));
}
