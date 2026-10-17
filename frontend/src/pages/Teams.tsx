import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api/client";
import type { Organization, Page, Team } from "../api/types";
import { Card, Empty, ErrorBox, Loading, PageHeader } from "../components/ui";

/** Overzicht van alle teams in de organisaties die de gebruiker kan zien. */
export function TeamsPage() {
  const orgs = useQuery({
    queryKey: ["orgs"],
    queryFn: () => api.get<Page<Organization>>("/api/v1/organizations", { limit: 200 }),
  });
  const teamQueries = useQueries({
    queries: (orgs.data?.items ?? []).map((o) => ({
      queryKey: ["teams", o.id],
      queryFn: () => api.get<Team[]>(`/api/v1/organizations/${o.id}/teams`),
    })),
  });
  if (orgs.isLoading) return <Loading />;
  const rows = (orgs.data?.items ?? []).flatMap((o, i) => (teamQueries[i]?.data ?? []).map((t) => ({ org: o, team: t })));
  return (
    <>
      <PageHeader title="Teams" subtitle="Teams groeperen leden binnen een organisatie. Maak teams aan vanuit een organisatie." />
      <Card>
        <ErrorBox error={orgs.error} />
        {rows.length === 0 ? (
          <Empty>Nog geen teams.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Team</th>
                  <th>Organisatie</th>
                  <th>Slug</th>
                  <th>Leden</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ org, team }) => (
                  <tr key={team.id}>
                    <td>
                      <Link to={`/organizations/${org.id}/teams/${team.id}`}>{team.name}</Link>
                    </td>
                    <td>
                      <Link to={`/organizations/${org.id}`}>{org.name}</Link>
                    </td>
                    <td className="mono small">
                      {org.slug}/{team.slug}
                    </td>
                    <td>{team.member_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
