// VaultX Invuller: in elke pagina (en elk frame). Zoekt loginformulieren en vraagt de
// achtergrond wat er mag. Die beslist op basis van de echte URL van dit frame (van de browser,
// niet van de pagina); hier komt enkel het ene item dat past.
//
// Drie stappen worden herkend:
//   password  een zichtbaar wachtwoordveld (met het gebruikersnaamveld ervoor)
//   username  enkel een gebruikersnaamveld (eerste stap van een login in twee stappen)
//   totp      een veld voor een eenmalige code (autocomplete="one-time-code", of otp/totp/code/
//             2fa/mfa in naam of label, met 6 tot 8 tekens)
(function () {
  "use strict";
  if (window.__vaultxInvuller) return;
  window.__vaultxInvuller = true;

  const ext = globalThis.browser ?? globalThis.chrome;
  const RETRY_CHECK_MS = 4000;
  const handled = new WeakSet(); // velden waarvoor we al vroegen
  let busy = false;
  let menu = null;

  // ---------------------------------------------------------------- velden zoeken

  /** querySelectorAll, ook in open shadow roots (web components zoals Authentik). */
  function deepAll(root, selector, out = []) {
    for (const el of root.querySelectorAll(selector)) out.push(el);
    for (const el of root.querySelectorAll("*")) if (el.shadowRoot) deepAll(el.shadowRoot, selector, out);
    return out;
  }

  function visible(el) {
    if (!el.isConnected || el.disabled || el.readOnly) return false;
    if (el.type === "hidden") return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none" && Number(st.opacity) > 0.05;
  }

  const TEXT_TYPES = new Set(["text", "email", "tel", ""]);
  const USER_HINT = /user|login|e-?mail|uid|ident|account|gebruiker|benutzer|utilisateur/i;
  const OTP_HINT = /otp|totp|code|2fa|mfa|token|verification|verificatie|tweestaps/i;
  const SEARCH_HINT = /search|zoek|query/i;

  function hints(el) {
    const label = el.labels && el.labels[0] ? el.labels[0].textContent : "";
    return [el.name, el.id, el.placeholder, el.getAttribute("aria-label"), label, el.className].join(" ");
  }

  function textInputs() {
    return deepAll(document, "input").filter((el) => TEXT_TYPES.has((el.getAttribute("type") || "").toLowerCase()) && visible(el));
  }

  function isOtp(el) {
    const ac = (el.getAttribute("autocomplete") || "").toLowerCase();
    if (ac === "one-time-code") return true;
    if (!OTP_HINT.test(hints(el))) return false;
    const max = Number(el.getAttribute("maxlength") || 0);
    const numeric = /numeric|decimal/.test(el.getAttribute("inputmode") || "") || /\\d|\[0-9\]/.test(el.getAttribute("pattern") || "");
    return (max >= 6 && max <= 8) || numeric;
  }

  function precedingUserField(pass, candidates) {
    const before = candidates.filter((el) => el.compareDocumentPosition(pass) & Node.DOCUMENT_POSITION_FOLLOWING);
    const sameForm = before.filter((el) => pass.form && el.form === pass.form);
    const pool = sameForm.length ? sameForm : before;
    const named = pool.filter((el) => (el.getAttribute("autocomplete") || "").includes("username") || USER_HINT.test(hints(el)));
    return (named.length ? named : pool).filter((el) => !SEARCH_HINT.test(hints(el))).pop() || null;
  }

  /** Wat staat er op de pagina? null als er geen loginstap is. */
  function detect() {
    const inputs = textInputs();
    const otp = inputs.find(isOtp);
    if (otp) return { stage: "totp", field: otp };
    const passwords = deepAll(document, "input[type=password]").filter(visible);
    const current = passwords.filter((el) => (el.getAttribute("autocomplete") || "") !== "new-password");
    if (current.length === 1 && passwords.length === 1) {
      const pass = current[0];
      return { stage: "password", field: pass, user: precedingUserField(pass, inputs.filter((e) => !isOtp(e))) };
    }
    if (passwords.length) return null; // registreren of wachtwoord wijzigen: niets doen
    const user = inputs.find(
      (el) => (el.getAttribute("autocomplete") || "").split(/\s+/).includes("username") ||
        (USER_HINT.test(hints(el)) && !SEARCH_HINT.test(hints(el)) && (el.form || el.closest("form"))),
    );
    return user ? { stage: "username", field: user } : null;
  }

  // ---------------------------------------------------------------- invullen

  function setValue(el, value) {
    el.focus();
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set ||
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(el, value); // langs de setter van het prototype, zodat React het ziet
    el.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  }

  function key(el, type) {
    const ev = new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true });
    el.dispatchEvent(ev);
    return ev.defaultPrevented;
  }

  const SUBMIT_TEXT = /log\s*in|sign\s*in|aanmelden|inloggen|anmelden|connexion|continue|verder|volgende|next|submit|verify|bevestig/i;

  function submitButton(el) {
    const scope = el.form || el.closest("form") || el.getRootNode();
    const buttons = [...scope.querySelectorAll("button, input[type=submit], [role=button]")].filter(visible);
    return buttons.find((b) => b.type === "submit") || buttons.find((b) => SUBMIT_TEXT.test(b.textContent || b.value || "")) || null;
  }

  /** "Enter": eerst als toets (voor apps die daarop letten), anders het formulier versturen. */
  async function pressEnter(el) {
    const url = location.href;
    const handledByPage = key(el, "keydown");
    key(el, "keypress");
    key(el, "keyup");
    if (handledByPage) return;
    await sleep(150);
    if (location.href !== url || !el.isConnected) return; // de pagina deed het zelf
    const form = el.form || el.closest("form");
    const button = submitButton(el);
    if (form) {
      if (button && form.contains(button) && button.type === "submit") form.requestSubmit(button);
      else form.requestSubmit();
    } else if (button) {
      button.click();
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function fill(found, answer) {
    const { creds } = answer;
    if (found.stage === "totp") setValue(found.field, creds.totp);
    else if (found.stage === "username") setValue(found.field, creds.username || "");
    else {
      if (found.user && creds.username) setValue(found.user, creds.username);
      setValue(found.field, creds.password || "");
    }
    if (!answer.submit) return;
    await sleep(250);
    await pressEnter(found.field);
    // Blijft hetzelfde veld staan (een app zonder nieuwe pagina), dan nog eens vragen: de
    // achtergrond ziet dan dat het formulier terugkwam en meldt dat de login mislukte.
    const field = found.field;
    setTimeout(() => {
      if (field.isConnected && visible(field) && document.visibilityState !== "hidden") ask(true);
    }, RETRY_CHECK_MS);
  }

  // ---------------------------------------------------------------- vragen aan de achtergrond

  async function send(msg) {
    try {
      return await ext.runtime.sendMessage(msg);
    } catch {
      return null; // extensie herladen of achtergrond weg
    }
  }

  async function ask(again = false) {
    if (busy) return;
    const found = detect();
    if (!found) return;
    if (!again && handled.has(found.field)) return;
    // Iets dat de gebruiker zelf typte, overschrijven we niet.
    if (!again && found.field.value && found.stage !== "totp") {
      handled.add(found.field);
      return;
    }
    handled.add(found.field);
    busy = true;
    try {
      const answer = await send({ type: "vx-form", stage: found.stage });
      if (!answer) return;
      if (answer.mode === "fill") await fill(found, answer);
      else if (answer.mode === "choose") showMenu(found, answer.items);
      else if (answer.mode === "failed") showBanner(answer.item);
    } finally {
      busy = false;
    }
  }

  // ---------------------------------------------------------------- keuzemenu en melding

  function host(styles) {
    const el = document.createElement("vaultx-invuller");
    el.style.cssText = "all:initial;position:fixed;z-index:2147483647;";
    const shadow = el.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = styles;
    shadow.append(style);
    document.documentElement.append(el);
    return { el, shadow };
  }

  const MENU_CSS = `
    .box{font:13px/1.35 system-ui,sans-serif;background:#fff;color:#1b2333;border:1px solid #c9d2e3;border-radius:8px;
      box-shadow:0 6px 24px rgba(0,0,0,.18);min-width:220px;max-width:340px;overflow:hidden}
    .head{display:flex;align-items:center;gap:6px;padding:6px 10px;background:#2f5bea;color:#fff;font-weight:600}
    .head span{flex:1}.x{all:unset;cursor:pointer;padding:0 4px;color:#fff}
    button.item{all:unset;display:block;box-sizing:border-box;width:100%;padding:7px 10px;cursor:pointer;border-top:1px solid #eef1f7}
    button.item:hover,button.item:focus{background:#eef3ff}
    .name{font-weight:600}.user{color:#5b6476;font-size:12px}`;

  function closeMenu() {
    if (menu) {
      menu.el.remove();
      menu.field.removeEventListener("keydown", menu.onKey, true);
    }
    menu = null;
  }

  function showMenu(found, items) {
    closeMenu();
    if (!items.length) return;
    menu = host(MENU_CSS);
    const r = found.field.getBoundingClientRect();
    menu.el.style.left = `${Math.max(4, r.left)}px`;
    menu.el.style.top = `${Math.min(window.innerHeight - 40, r.bottom + 4)}px`;
    const box = document.createElement("div");
    box.className = "box";
    box.setAttribute("role", "menu");
    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML = "<span>VaultX: kies een login</span>";
    const x = document.createElement("button");
    x.className = "x";
    x.textContent = "×";
    x.title = "Sluiten";
    x.onclick = closeMenu;
    head.append(x);
    box.append(head);
    for (const item of items) {
      const b = document.createElement("button");
      b.className = "item";
      b.setAttribute("role", "menuitem");
      const n = document.createElement("div");
      n.className = "name";
      n.textContent = item.name;
      const u = document.createElement("div");
      u.className = "user";
      u.textContent = item.username || "";
      b.append(n, u);
      b.onclick = async () => {
        closeMenu();
        const answer = await send({ type: "vx-choose", itemId: item.id, stage: found.stage });
        if (answer?.mode === "fill") await fill(found, answer);
      };
      box.append(b);
    }
    menu.shadow.append(box);
    // Met het toetsenbord: pijl omlaag vanuit het veld, pijlen in het menu, Enter kiest, Escape sluit.
    const buttons = [...box.querySelectorAll("button.item")];
    const move = (i) => buttons[(i + buttons.length) % buttons.length].focus();
    box.addEventListener("keydown", (e) => {
      const i = buttons.indexOf(menu && menu.shadow.activeElement);
      if (e.key === "ArrowDown") move(i + 1);
      else if (e.key === "ArrowUp") move(i - 1);
      else if (e.key === "Escape") {
        closeMenu();
        found.field.focus();
      } else return;
      e.preventDefault();
    });
    menu.field = found.field;
    menu.onKey = (e) => {
      if (e.key === "ArrowDown" && menu) {
        e.preventDefault();
        move(0);
      } else if (e.key === "Escape") closeMenu();
    };
    found.field.addEventListener("keydown", menu.onKey, true);
  }

  function showBanner(item) {
    const b = host(`.b{font:13px/1.4 system-ui,sans-serif;background:#fff4e5;color:#5a3b00;border:1px solid #f0b55a;
      border-radius:8px;padding:10px 12px;box-shadow:0 6px 24px rgba(0,0,0,.15);max-width:360px}
      .t{font-weight:600}.x{all:unset;float:right;cursor:pointer;margin-left:8px}`);
    // Voor schermlezers (en de test): de melding zonder itemnaam op het element zelf.
    b.el.setAttribute("role", "alert");
    b.el.setAttribute("aria-label", "VaultX: inloggen lukte niet. Wachtwoord klopt niet meer?");
    b.el.style.top = "12px";
    b.el.style.right = "12px";
    const div = document.createElement("div");
    div.className = "b";
    const x = document.createElement("button");
    x.className = "x";
    x.textContent = "×";
    x.onclick = () => b.el.remove();
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = "VaultX: inloggen lukte niet";
    const p = document.createElement("div");
    p.textContent = `${item ? `"${item.name}": ` : ""}wachtwoord klopt niet meer? VaultX probeert het niet opnieuw. Pas het item aan in je kluis.`;
    div.append(x, t, p);
    b.shadow.append(div);
    setTimeout(() => b.el.remove(), 30000);
  }

  // ---------------------------------------------------------------- starten

  let timer = null;
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(() => ask(false), 300);
  }

  ext.runtime.onMessage.addListener((msg) => {
    if (msg?.type === "vx-fill-now") {
      const found = detect();
      if (found) handled.delete(found.field);
      ask(true);
    }
  });

  document.addEventListener("pointerdown", (e) => {
    if (menu && !e.composedPath().includes(menu.el)) closeMenu();
  }, true);
  new MutationObserver(schedule).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["type", "style", "class", "hidden"] });
  schedule();
})();
