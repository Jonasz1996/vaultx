const dtf = new Intl.DateTimeFormat("nl-BE", { dateStyle: "medium", timeStyle: "short" });
const rtf = new Intl.RelativeTimeFormat("nl", { numeric: "auto" });
export function formatDate(value) {
    return value ? dtf.format(new Date(value)) : "—";
}
export function relative(value) {
    if (!value)
        return "nooit";
    const diff = (new Date(value).getTime() - Date.now()) / 1000;
    const abs = Math.abs(diff);
    if (abs < 60)
        return rtf.format(Math.round(diff), "second");
    if (abs < 3600)
        return rtf.format(Math.round(diff / 60), "minute");
    if (abs < 86400)
        return rtf.format(Math.round(diff / 3600), "hour");
    return rtf.format(Math.round(diff / 86400), "day");
}
export function userLabel(u) {
    return u.display_name || u.email || u.username || "(onbekend)";
}
export const roleLabels = {
    owner: "Eigenaar",
    admin: "Beheerder",
    member: "Lid",
    maintainer: "Maintainer",
};
export const authLabels = {
    forward_auth: "Authentik forward auth",
    oidc: "OIDC",
    saml: "SAML",
    header: "Proxy-headers",
    access_list: "NPM access list",
    app: "Eigen login",
    none: "Geen",
    unknown: "Onbekend",
};
export const statusInfo = {
    protected: { label: "Beschermd", tone: "good", hint: "Aanmelden verloopt via Authentik" },
    restricted: { label: "Beperkt", tone: "info", hint: "Afgeschermd, maar niet via Authentik" },
    unprotected: { label: "Open", tone: "bad", hint: "Bewust zonder aanmelding" },
    unknown: { label: "Onbekend", tone: "warn", hint: "Aanmelding nog niet bepaald: zet een label of vul het in" },
    offline: { label: "Offline", tone: "bad", hint: "Host uitgeschakeld of nginx-fout in NPM" },
    removed: { label: "Verdwenen", tone: "neutral", hint: "Host staat niet meer in NPM" },
};
export const changeStatusInfo = {
    running: { label: "Bezig", tone: "info", hint: "VaultX is de host aan het wijzigen" },
    applied: { label: "Uitgevoerd", tone: "good", hint: "De wijziging staat in NPM" },
    rolled_back: { label: "Teruggezet", tone: "warn", hint: "De controle faalde; de vorige config staat er weer" },
    rollback_failed: {
        label: "Terugzetten mislukt",
        tone: "bad",
        hint: "Zet de vorige config met de hand terug in NPM (zie details)",
    },
    refused: { label: "Niet uitgevoerd", tone: "neutral", hint: "VaultX heeft niets gewijzigd" },
    interrupted: { label: "Onderbroken", tone: "bad", hint: "Kijk de host na in NPM" },
};
export const actionLabels = {
    protect: "Beschermen",
    unprotect: "Bescherming weghalen",
    publish: "Publiceren",
    unpublish: "Depubliceren",
    create: "Aanmaken",
    update: "Bewerken",
    delete: "Verwijderen",
    enable: "Aanzetten",
    disable: "Uitzetten",
    renew: "Certificaat vernieuwen",
    upload: "Certificaat opladen",
};
