const dtf = new Intl.DateTimeFormat("nl-BE", { dateStyle: "medium", timeStyle: "short" });
const rtf = new Intl.RelativeTimeFormat("nl", { numeric: "auto" });

export function formatDate(value: string | null | undefined): string {
  return value ? dtf.format(new Date(value)) : "—";
}

export function relative(value: string | null | undefined): string {
  if (!value) return "nooit";
  const diff = (new Date(value).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export function userLabel(u: { display_name: string | null; email: string | null; username: string | null }) {
  return u.display_name || u.email || u.username || "(onbekend)";
}

export const roleLabels: Record<string, string> = {
  owner: "Eigenaar",
  admin: "Beheerder",
  member: "Lid",
  maintainer: "Maintainer",
};
