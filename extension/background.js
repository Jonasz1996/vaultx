// VaultX Invuller: achtergrond (service worker in Chrome/Edge, achtergrondscript in Firefox).
//
// Hier leeft alles wat geheim is. De content scripts in de pagina's krijgen enkel de gegevens
// van één item, en enkel als de browser zelf (niet de pagina) bevestigt dat het frame exact op
// een host van dat item staat en niet in een vreemd iframe zit.
//
// Opslag:
//   storage.local    instellingen, refresh token, versleutelde kluis (zoals /api/sync ze gaf),
//                    versleutelde user key en KDF-instellingen (offline ontgrendelen)
//   storage.session  de ontsleutelde user key en het access token; weg als de browser sluit
//
// Valt VaultX weg, dan blijft de laatst gesynchroniseerde (versleutelde) kluis bruikbaar en
// ontgrendel je met je master password, zonder server.

/* global importScripts, VXBw, VXMatch, VXTotp */
if (typeof importScripts === "function") {
  importScripts("lib/vendor/argon2.umd.min.js", "lib/bw.js", "lib/match.js", "lib/totp.js");
}

const ext = globalThis.browser ?? globalThis.chrome;
const LOCK_AFTER_MS = 12 * 3600 * 1000;
const SYNC_MINUTES = 5;
const SUBMIT_PAUSE_MS = 30 * 1000;
const DEVICE_TYPES = { chrome: 2, firefox: 3, edge: 5, opera: 4 };

// ------------------------------------------------------------------ opslag

const local = {
  get: async (k) => (await ext.storage.local.get(k))[k],
  set: (k, v) => ext.storage.local.set({ [k]: v }),
  remove: (k) => ext.storage.local.remove(k),
};
const session = {
  get: async (k) => (await ext.storage.session.get(k))[k],
  set: (k, v) => ext.storage.session.set({ [k]: v }),
  clear: () => ext.storage.session.clear(),
};

function browserName() {
  const ua = navigator.userAgent;
  if (/Edg\//.test(ua)) return "edge";
  if (/Firefox\//.test(ua)) return "firefox";
  if (/OPR\//.test(ua)) return "opera";
  return "chrome";
}

async function deviceId() {
  let id = await local.get("deviceId");
  if (!id) {
    id = crypto.randomUUID();
    await local.set("deviceId", id);
  }
  return id;
}

// ------------------------------------------------------------------ server

class ApiError extends Error {
  constructor(status, message, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function errorMessage(data, fallback) {
  const model = data?.ErrorModel ?? data?.errorModel;
  if (typeof model?.Message === "string") return model.Message;
  if (typeof data?.message === "string") return data.message;
  if (typeof data?.error_description === "string") return data.error_description;
  return fallback;
}

async function serverUrl() {
  const cfg = await local.get("cfg");
  if (!cfg?.server) throw new ApiError(0, "Nog niet ingesteld");
  return cfg.server;
}

async function tokenRequest(form) {
  const res = await fetch(`${await serverUrl()}/identity/connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams(form).toString(),
    credentials: "omit",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, `Fout ${res.status}`), data);
  return data;
}

async function saveAccess(data) {
  await session.set("access", { token: data.access_token, exp: Date.now() + (Number(data.expires_in) || 3600) * 1000 });
  if (data.refresh_token) {
    const auth = (await local.get("auth")) || {};
    await local.set("auth", { ...auth, refresh: data.refresh_token });
  }
}

async function accessToken(force = false) {
  const cur = await session.get("access");
  if (!force && cur && cur.exp - Date.now() > 60_000) return cur.token;
  const auth = await local.get("auth");
  if (!auth?.refresh) throw new ApiError(401, "Afgemeld bij VaultX");
  const data = await tokenRequest({ grant_type: "refresh_token", refresh_token: auth.refresh, client_id: "browser" });
  await saveAccess(data);
  return data.access_token;
}

async function api(method, path, body) {
  const send = async (token) =>
    fetch(`${await serverUrl()}${path}`, {
      method,
      credentials: "omit",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  let res = await send(await accessToken());
  if (res.status === 401) res = await send(await accessToken(true));
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, `Fout ${res.status}`), data);
  return data;
}

// ------------------------------------------------------------------ aanmelden en ontgrendelen

async function prelogin(server, email) {
  const res = await fetch(`${server}/identity/accounts/prelogin/password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ email }),
    credentials: "omit",
  });
  if (!res.ok) throw new ApiError(res.status, `VaultX antwoordt niet zoals verwacht (${res.status})`);
  const d = await res.json();
  return {
    salt: d.salt || email,
    kdf: d.kdf,
    iterations: d.kdfIterations,
    memory: d.kdfMemory,
    parallelism: d.kdfParallelism,
  };
}

function normalizeServer(raw) {
  let s = String(raw || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(s)) s = "https://" + s;
  const u = new URL(s);
  return `${u.protocol}//${u.host}${u.pathname.replace(/\/+$/, "")}`;
}

/** Eerste keer: aanmelden met e-mail + master password (+ tweestapscode) via de Bitwarden-API. */
async function login({ server, email, password, code, remember }) {
  server = normalizeServer(server);
  email = String(email || "").trim().toLowerCase();
  const params = await prelogin(server, email);
  const mkey = await VXBw.masterKey(params, password);
  try {
    const hash = await VXBw.loginHash(mkey, password);
    await local.set("cfg", { server, email });
    const name = browserName();
    const form = {
      grant_type: "password",
      username: email,
      password: hash,
      scope: "api offline_access",
      client_id: "browser",
      deviceType: String(DEVICE_TYPES[name] ?? 2),
      deviceIdentifier: await deviceId(),
      deviceName: `VaultX Invuller (${name[0].toUpperCase()}${name.slice(1)})`,
    };
    const remembered = (await local.get("twoFactorRemember"))?.[email];
    if (code) {
      const c = String(code).replace(/\s+/g, "");
      Object.assign(form, {
        twoFactorToken: c,
        twoFactorProvider: /^\d{6,8}$/.test(c) ? "0" : "8",
        twoFactorRemember: remember ? "1" : "0",
      });
    } else if (remembered) {
      Object.assign(form, { twoFactorToken: remembered, twoFactorProvider: "5", twoFactorRemember: "0" });
    }
    let data;
    try {
      data = await tokenRequest(form);
    } catch (err) {
      const providers = err.data?.TwoFactorProviders2 ?? err.data?.twoFactorProviders2;
      if (providers) {
        if (remembered && !code) await local.set("twoFactorRemember", { [email]: undefined });
        return { twoFactor: true, authenticator: "0" in providers, securityKeyOnly: !("0" in providers) };
      }
      throw err;
    }
    if (data.TwoFactorToken) await local.set("twoFactorRemember", { [email]: data.TwoFactorToken });
    const encUserKey = String(data.Key ?? data.key ?? "");
    const userKey = await VXBw.openUserKey(mkey, encUserKey);
    await local.set("auth", { refresh: data.refresh_token, encUserKey, params });
    await saveAccess(data);
    await setUnlocked(userKey);
    await sync().catch(() => undefined);
    return { ok: true };
  } finally {
    mkey.fill(0);
  }
}

/** Ontgrendelen met het master password, zonder server (de user key staat versleuteld lokaal). */
async function unlockWithPassword(password) {
  const auth = await local.get("auth");
  if (!auth?.encUserKey) throw new Error("Meld je eerst aan");
  const mkey = await VXBw.masterKey(auth.params, password);
  try {
    let userKey;
    try {
      userKey = await VXBw.openUserKey(mkey, auth.encUserKey);
    } catch {
      throw new Error("Master password klopt niet");
    }
    await setUnlocked(userKey);
  } finally {
    mkey.fill(0);
  }
  sync().catch(() => undefined);
  return { ok: true };
}

async function setUnlocked(userKey, via = "password") {
  await session.set("unlock", { key: VXBw.toB64(userKey), at: Date.now(), via });
  await ext.storage.session.remove(["lockReason", "manualLock"]);
  // Na een ontgrendeling met het master password: dit apparaat ook laten ontgrendelen met
  // Authentik (als dat kan). Op de achtergrond; mislukt het, dan blijft alles gewoon werken.
  if (via === "password") registerDevice(userKey.slice()).catch(() => undefined);
  userKey.fill(0);
  cache = null;
  ext.alarms.create("lock", { when: Date.now() + LOCK_AFTER_MS });
  connectHub();
}

async function userKey() {
  const u = await session.get("unlock");
  if (!u) return null;
  if (Date.now() - u.at > LOCK_AFTER_MS) {
    await lock();
    return null;
  }
  return VXBw.fromB64(u.key);
}

async function lock(reason, { manual = false } = {}) {
  await ext.storage.session.remove("unlock");
  if (reason) await session.set("lockReason", reason);
  // Zelf vergrendeld: niet meteen weer openen met Authentik, pas na een klik op de knop.
  if (manual) await session.set("manualLock", true);
  cache = null;
  pending.clear();
}

async function logout() {
  try {
    const du = await local.get("deviceUnlock");
    if (du) await webRequest("DELETE", `${UNLOCK_API}/${du.id}`).catch(() => undefined);
    await local.remove("deviceUnlock");
    await local.remove("deviceUnlockInfo");
    await ext.storage.session.clear();
    await local.remove("auth");
    await local.remove("vault");
  } finally {
    cache = null;
    disconnectHub();
  }
}

// ------------------------------------------------------------------ ontgrendelen met Authentik
//
// De helft van de sleutel op dit apparaat (D, enkel in storage.local), de helft in VaultX (de
// user key versleuteld met D). Openen vraagt een VaultX-sessie die via Authentik begon, met MFA.
// De extensie gebruikt de sessie van de browser (cookie); is die er niet, dan meldt ze zich stil
// aan via Authentik (prompt=none), net zoals de VaultX-webpagina. Lukt dat niet (Authentik plat,
// geen MFA, apparaat ingetrokken), dan vraagt ze gewoon het master password.

const UNLOCK_API = "/api/v1/vault/device-unlock";
let lastAuthentikTry = 0;

/** Request naar VaultX met de browsersessie (cookie), niet met het Bitwarden-token. */
async function webRequest(method, path, body) {
  const res = await fetch(`${await serverUrl()}${path}`, {
    method,
    credentials: "include",
    headers: {
      Accept: "application/json",
      "X-VaultX-CSRF": "1",
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) throw new ApiError(res.status, data?.detail || `Fout ${res.status}`, data);
  return data;
}

/**
 * Stil aanmelden bij VaultX via Authentik (prompt=none) in een tabblad op de achtergrond. Een
 * gewone fetch kan dat niet: de cookies van een login over twee sites heen (VaultX -> Authentik ->
 * VaultX) bewaart de browser enkel bij een echte navigatie. Het tabblad toont nooit het
 * aanmeldscherm van Authentik (silent=only) en sluit vanzelf.
 */
async function silentLogin() {
  const server = await serverUrl();
  const url = `${server}/auth/login?silent=only&next=${encodeURIComponent("/api/v1/me")}`;
  const tab = await ext.tabs.create({ url, active: false });
  try {
    await new Promise((resolve) => {
      const done = () => {
        ext.tabs.onUpdated.removeListener(listener);
        clearTimeout(timer);
        resolve();
      };
      const listener = (id, info, t) => {
        if (id !== tab.id || !t.url || info.status !== "complete") return;
        const u = new URL(t.url);
        if (u.origin === new URL(server).origin && !u.pathname.startsWith("/auth/")) done();
      };
      const timer = setTimeout(done, 20_000);
      ext.tabs.onUpdated.addListener(listener);
    });
  } finally {
    await ext.tabs.remove(tab.id).catch(() => undefined);
  }
}

/** Zelfde als webRequest, maar zonder sessie eerst stil aanmelden via Authentik. */
async function withWebSession(method, path, body) {
  try {
    return await webRequest(method, path, body);
  } catch (err) {
    if (err.status !== 401) throw err;
  }
  await silentLogin();
  return webRequest(method, path, body);
}

async function registerDevice(userKey) {
  try {
    const existing = await local.get("deviceUnlock");
    if (existing) return;
    const st = await withWebSession("GET", UNLOCK_API);
    if (!st?.eligible) {
      await local.set("deviceUnlockInfo", { reason: st?.reason, message: st?.message, at: Date.now() });
      return;
    }
    const d = crypto.getRandomValues(new Uint8Array(64));
    const blob = await VXBw.encryptBytes(userKey, d);
    const name = browserName();
    const res = await webRequest("POST", UNLOCK_API, {
      device_identifier: await deviceId(),
      name: `VaultX Invuller (${name[0].toUpperCase()}${name.slice(1)})`,
      blob,
    });
    await local.set("deviceUnlock", { id: res.id, key: VXBw.toB64(d) });
    await local.remove("deviceUnlockInfo");
    d.fill(0);
  } catch (err) {
    const message =
      err?.status === 401
        ? "niet aangemeld bij VaultX via Authentik. Meld je aan en ontgrendel opnieuw."
        : err?.message || String(err);
    await local.set("deviceUnlockInfo", { message, at: Date.now() });
  } finally {
    userKey.fill(0);
  }
}

/**
 * Kluis openen met Authentik. Geeft true als het lukte. Loopt er al een poging (bv. het
 * popupvenster en een formulier tegelijk), dan wacht deze op dezelfde.
 * force: niet wachten op de pauze van 60 s; explicit: ook na zelf vergrendelen (de knop).
 */
let authentikUnlocking = null;
function unlockWithAuthentik(opts = {}) {
  if (!authentikUnlocking) {
    authentikUnlocking = tryAuthentikUnlock(opts).finally(() => {
      authentikUnlocking = null;
    });
  }
  return authentikUnlocking;
}

async function tryAuthentikUnlock({ force = false, explicit = false } = {}) {
  const du = await local.get("deviceUnlock");
  if (!du || (await session.get("unlock"))) return Boolean(du && (await session.get("unlock")));
  if (!explicit && (await session.get("manualLock"))) return false;
  if (!force && Date.now() - lastAuthentikTry < 60_000) return false;
  lastAuthentikTry = Date.now();
  let res;
  try {
    res = await withWebSession("POST", `${UNLOCK_API}/${du.id}/open`, {});
  } catch (err) {
    if (err.status === 403 || err.status === 404) {
      await local.set("deviceUnlockInfo", { message: err.message, at: Date.now() });
      // Ingetrokken of weg: de apparaatsleutel is waardeloos geworden.
      if (err.status === 404 || /ingetrokken/i.test(err.message)) await local.remove("deviceUnlock");
    }
    return false;
  }
  const d = VXBw.fromB64(du.key);
  try {
    const userKey = await VXBw.decryptBytes(res.blob, d);
    if (userKey.length !== 64) throw new Error("Onverwachte sleutel");
    await local.remove("deviceUnlockInfo");
    await setUnlocked(userKey, "authentik");
    sync().catch(() => undefined);
    return true;
  } catch {
    await local.remove("deviceUnlock");
    return false;
  } finally {
    d.fill(0);
  }
}

/**
 * Ontgrendeld via Authentik: blijft dat zo? Eindigde de Authentik-sessie (afmelden, back-channel
 * logout) of werd het apparaat ingetrokken, dan vergrendelt de extensie. Is VaultX gewoon
 * onbereikbaar, dan niet (standalone).
 */
async function checkAuthentikUnlock() {
  const u = await session.get("unlock");
  const du = await local.get("deviceUnlock");
  if (!u || u.via !== "authentik" || !du) return;
  try {
    const st = await withWebSession("GET", `${UNLOCK_API}/${du.id}`);
    if (!st.active || !st.eligible) await lock(st.message || "Ontgrendelen met Authentik geldt niet meer.");
  } catch (err) {
    if (err.status === 401) await lock("Je bent afgemeld bij Authentik.");
    else if (err.status === 404) {
      await local.remove("deviceUnlock");
      await lock("Dit apparaat werd ingetrokken.");
    }
  }
}

// ------------------------------------------------------------------ kluis

let cache = null; // { rev, items } ontsleutelde login-items, enkel in het geheugen
let syncing = null;

async function sync() {
  if (syncing) return syncing;
  syncing = (async () => {
    const data = await api("GET", "/api/sync?excludeDomains=true");
    const ciphers = (data.ciphers || data.Ciphers || []).filter((c) => c.type === 1);
    await local.set("vault", { ciphers, syncedAt: Date.now() });
    const key = data.profile?.key;
    const auth = await local.get("auth");
    if (key && auth && key !== auth.encUserKey) await local.set("auth", { ...auth, encUserKey: key });
    cache = null;
    return { count: ciphers.length };
  })();
  try {
    return await syncing;
  } finally {
    syncing = null;
  }
}

async function items() {
  const key = await userKey();
  if (!key) return null;
  const vault = await local.get("vault");
  const rev = vault?.syncedAt ?? 0;
  if (cache && cache.rev === rev) return cache.items;
  const out = [];
  for (const raw of vault?.ciphers || []) {
    try {
      const item = await VXBw.decryptLogin(raw, key);
      if (item) out.push(item);
    } catch {
      /* item met een kapotte itemsleutel: overslaan */
    }
  }
  key.fill(0);
  cache = { rev, items: out };
  return out;
}

// ------------------------------------------------------------------ live sync (SignalR, json)

let hub = null;
let hubRetry = 0;

async function connectHub() {
  if (hub || !(await local.get("auth"))) return;
  let token;
  try {
    token = await accessToken();
  } catch {
    return;
  }
  const url = (await serverUrl()).replace(/^http/, "ws") + `/notifications/hub?access_token=${encodeURIComponent(token)}`;
  let ws;
  try {
    ws = new WebSocket(url);
  } catch {
    return;
  }
  hub = ws;
  ws.onopen = () => ws.send('{"protocol":"json","version":1}\x1e');
  ws.onmessage = (ev) => {
    hubRetry = 0;
    for (const part of String(ev.data).split("\x1e")) {
      if (!part.trim()) continue;
      let msg;
      try {
        msg = JSON.parse(part);
      } catch {
        continue;
      }
      if (msg.type === 1 && Array.isArray(msg.arguments)) {
        const type = msg.arguments[0]?.Type;
        if (type === 11) logout(); // LogOut: dit apparaat werd afgemeld
        else sync().catch(() => undefined);
      } else if (msg.type === 6) {
        ws.send('{"type":6}\x1e');
      }
    }
  };
  ws.onclose = () => {
    hub = null;
    hubRetry = Math.min(hubRetry + 1, 6);
    ext.alarms.create("hub", { delayInMinutes: Math.max(0.5, 2 ** hubRetry / 4) });
  };
}

function disconnectHub() {
  if (hub) {
    hub.onclose = null;
    hub.close();
    hub = null;
  }
}

// ------------------------------------------------------------------ frames controleren

/**
 * Zit dit frame in een pagina van dezelfde origin, tot bovenaan? De browser geeft de URL van
 * elk frame; de pagina kan daar niet over liegen. Een frame van een andere origin (of een
 * pagina die zelf in een vreemd iframe zit) krijgt nooit iets.
 */
async function frameIsTrusted(sender) {
  const origin = VXMatch.originOf(sender.url);
  if (!origin || !sender.tab) return false;
  if (VXMatch.originOf(sender.tab.url) !== origin) return false;
  if (!sender.frameId) return true;
  if (!ext.webNavigation?.getAllFrames) return false;
  const frames = await ext.webNavigation.getAllFrames({ tabId: sender.tab.id });
  const byId = new Map((frames || []).map((f) => [f.frameId, f]));
  let f = byId.get(sender.frameId);
  for (let guard = 0; f && guard < 50; guard++) {
    if (f.frameId === 0) return VXMatch.originOf(f.url) === origin;
    if (VXMatch.originOf(f.url) !== origin) return false;
    f = byId.get(f.parentFrameId);
  }
  return false;
}

// ------------------------------------------------------------------ invullen

// Recente automatische verzendingen: per tabblad, site en stap. Komt hetzelfde formulier
// binnen 30 seconden terug, dan lukte de login niet: niet opnieuw proberen.
const submits = new Map();
const pending = new Map(); // tabId -> itemId dat de gebruiker in het popupvenster koos

// Per tabblad, site en stap (niet per pad: na een fout wachtwoord staat het formulier vaak op
// een ander pad, zoals /login na een POST).
function pageKey(sender, stage) {
  return `${sender.tab.id}|${new URL(sender.url).origin}|${stage}`;
}

function publicItem(i) {
  return { id: i.id, name: i.name || "(zonder naam)", username: i.username };
}

async function credentialsFor(item, stage) {
  if (stage === "totp") {
    const t = await VXTotp.totpCode(item.totp);
    return t ? { totp: t.code } : null;
  }
  return { username: item.username, password: stage === "username" ? undefined : item.password };
}

/** Mislukte login onthouden; telt niet meer zodra het item gewijzigd is (ander wachtwoord). */
async function recordFailure(item, sender) {
  const failures = (await local.get("failures")) || {};
  failures[item.id] = { host: new URL(sender.url).host, at: Date.now(), revision: item.revisionDate };
  await local.set("failures", failures);
}

/** De content script meldt een loginformulier (stap username, password of totp). */
async function onForm(msg, sender) {
  if (!(await frameIsTrusted(sender))) return { mode: "none", reason: "frame" };
  let all = await items();
  if (!all && (await unlockWithAuthentik())) all = await items();
  if (!all) return { mode: "locked" };
  const stage = msg.stage;
  let candidates = VXMatch.matchingItems(all, sender.url);
  if (stage === "totp") candidates = candidates.filter((i) => i.totp && VXTotp.parseTotp(i.totp));
  if (!candidates.length) return { mode: "none" };

  // Gekozen in het popupvenster of het keuzemenu: dat item, zonder de automatische regels.
  const chosen = pending.get(sender.tab.id);
  if (chosen) {
    const item = candidates.find((i) => i.id === chosen.itemId);
    if (item && Date.now() - chosen.at < 60_000) {
      pending.delete(sender.tab.id);
      return fillAnswer(item, stage, sender, true);
    }
  }

  const key = pageKey(sender, stage);
  const last = submits.get(key);
  if (last && Date.now() - last.at < SUBMIT_PAUSE_MS) {
    // Hetzelfde formulier kwam terug na onze verzending: de login lukte niet.
    const item = candidates.find((i) => i.id === last.itemId);
    if (item && stage !== "totp") await recordFailure(item, sender);
    return { mode: "failed", item: item ? publicItem(item) : null };
  }

  const auto = candidates.filter((i) => i.autofill && !i.reprompt);
  if (auto.length === 1 && candidates.length === 1) return fillAnswer(auto[0], stage, sender, false);
  // Meerdere passende items (of items die niet automatisch mogen): het keuzemenu bij het veld.
  const choices = candidates.filter((i) => !i.reprompt).map(publicItem);
  choices.sort((a, b) => a.name.localeCompare(b.name, "nl") || (a.username || "").localeCompare(b.username || "", "nl"));
  return { mode: "choose", items: choices };
}

async function fillAnswer(item, stage, sender, chosenByUser) {
  if (item.reprompt) return { mode: "none", reason: "reprompt" };
  const creds = await credentialsFor(item, stage);
  if (!creds) return { mode: "none" };
  const submit = item.submit;
  if (submit) submits.set(pageKey(sender, stage), { at: Date.now(), itemId: item.id });
  return { mode: "fill", item: publicItem(item), creds, submit, chosenByUser };
}

/** Keuzemenu: de gebruiker koos een item voor dit formulier. */
async function onChoose(msg, sender) {
  if (!(await frameIsTrusted(sender))) return { mode: "none" };
  const all = await items();
  if (!all) return { mode: "locked" };
  const item = VXMatch.matchingItems(all, sender.url).find((i) => i.id === msg.itemId);
  if (!item) return { mode: "none" };
  return fillAnswer(item, msg.stage, sender, true);
}

// ------------------------------------------------------------------ popupvenster

async function status() {
  const cfg = (await local.get("cfg")) || {};
  const auth = await local.get("auth");
  const vault = await local.get("vault");
  let unlocked = Boolean(await userKey());
  if (!unlocked && auth) unlocked = await unlockWithAuthentik({ force: true });
  const du = await local.get("deviceUnlock");
  const info = await local.get("deviceUnlockInfo");
  const u = await session.get("unlock");
  return {
    authentik: { registered: Boolean(du), message: info?.message || null, via: u?.via || null },
    lockReason: unlocked ? null : (await session.get("lockReason")) || null,
    manualLock: !unlocked && Boolean(await session.get("manualLock")),
    configured: Boolean(auth),
    server: cfg.server || "",
    email: cfg.email || "",
    unlocked,
    syncedAt: vault?.syncedAt ?? null,
    count: vault?.ciphers?.length ?? 0,
  };
}

async function tabItems(tabId) {
  const tab = await ext.tabs.get(tabId);
  const all = await items();
  if (!all || !tab?.url) return [];
  return VXMatch.matchingItems(all, tab.url).map((i) => ({ ...publicItem(i), reprompt: i.reprompt }));
}

/** Popup: "Invullen" voor een item op het huidige tabblad. */
async function fillTab(tabId, itemId) {
  pending.set(tabId, { itemId, at: Date.now() });
  try {
    await ext.tabs.sendMessage(tabId, { type: "vx-fill-now" }, { frameId: 0 });
  } catch {
    /* geen content script (bv. interne pagina) */
  }
  return { ok: true };
}

// ------------------------------------------------------------------ berichten

const handlers = {
  // content scripts
  "vx-form": onForm,
  "vx-choose": onChoose,
  // popupvenster (enkel vanuit de extensie zelf)
  "vx-status": () => status(),
  "vx-login": (m) => login(m),
  "vx-unlock": (m) => unlockWithPassword(m.password),
  "vx-lock": () => lock(null, { manual: true }).then(() => ({ ok: true })),
  "vx-logout": () => logout().then(() => ({ ok: true })),
  "vx-sync": () => sync(),
  "vx-unlock-authentik": () => unlockWithAuthentik({ force: true, explicit: true }).then((ok) => ({ ok })),
  "vx-check-authentik": () => checkAuthentikUnlock().then(() => ({ ok: true })),
  "vx-tab-items": (m) => tabItems(m.tabId),
  "vx-fill-tab": (m) => fillTab(m.tabId, m.itemId),
};
const POPUP_ONLY = new Set(["vx-unlock-authentik", "vx-check-authentik", "vx-status", "vx-login", "vx-unlock", "vx-lock", "vx-logout", "vx-sync", "vx-tab-items", "vx-fill-tab"]);

ext.runtime.onMessage.addListener((msg, sender, reply) => {
  const handler = handlers[msg?.type];
  if (!handler) return false;
  // Het popupvenster is een pagina van de extensie zelf; content scripts hebben de URL van de site.
  const fromExtension = sender.id === ext.runtime.id && String(sender.url || "").startsWith(ext.runtime.getURL(""));
  if (POPUP_ONLY.has(msg.type) !== fromExtension) return false;
  if (!fromExtension && !sender.tab) return false;
  Promise.resolve()
    .then(() => handler(msg, sender))
    .then(reply, (err) => reply({ error: err?.message || String(err), status: err?.status }));
  return true;
});

ext.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "lock") await lock();
  if (alarm.name === "sync" && (await local.get("auth"))) {
    sync().catch(() => undefined);
    checkAuthentikUnlock().catch(() => undefined);
  }
  if (alarm.name === "hub") connectHub();
});

ext.alarms.create("sync", { periodInMinutes: SYNC_MINUTES });
ext.runtime.onStartup?.addListener(() => {
  unlockWithAuthentik({ force: true }).catch(() => undefined);
  sync().catch(() => undefined);
  connectHub();
});
connectHub();

// Voor de tests (Node): de functies zonder browser.
if (typeof module !== "undefined" && module.exports) module.exports = { normalizeServer };
