// Popupvenster van VaultX Invuller: instellen, ontgrendelen en invullen op het huidige tabblad.
// Alle geheimen blijven in de achtergrond; hier komen enkel namen en gebruikersnamen.
(function () {
  "use strict";
  const ext = globalThis.browser ?? globalThis.chrome;
  const app = document.getElementById("app");

  async function call(type, extra = {}) {
    const res = await ext.runtime.sendMessage({ type, ...extra });
    if (res && res.error) throw new Error(res.error);
    return res;
  }

  function h(tag, attrs = {}, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (const c of children.flat()) if (c != null) el.append(c);
    return el;
  }

  function field(label, attrs) {
    return h("label", {}, h("span", {}, label), h("input", attrs));
  }

  function errorBox() {
    return h("p", { class: "error", role: "alert", hidden: true });
  }

  function showError(box, err) {
    box.textContent = err ? err.message || String(err) : "";
    box.hidden = !err;
  }

  function ago(ts) {
    if (!ts) return "nog nooit";
    const min = Math.round((Date.now() - ts) / 60000);
    if (min < 1) return "zonet";
    if (min < 60) return `${min} min geleden`;
    return new Date(ts).toLocaleString("nl-BE");
  }

  async function activeTab() {
    const [tab] = await ext.tabs.query({ active: true, lastFocusedWindow: true });
    return tab;
  }

  // ---------------------------------------------------------------- schermen

  function setupView(status, twoFactor) {
    const err = errorBox();
    const form = h(
      "form",
      {},
      h("p", { class: "muted" }, "Meld je één keer aan met je VaultX-kluis (e-mail en master password)."),
      field("Adres van VaultX", { name: "server", type: "url", required: true, placeholder: "https://vaultx.example.be", value: status.server || "" }),
      field("E-mail", { name: "email", type: "email", required: true, autocomplete: "username", value: status.email || "" }),
      field("Master password", { name: "password", type: "password", required: true, autocomplete: "current-password" }),
      twoFactor
        ? [
            field(twoFactor.authenticator ? "Code uit je authenticator-app (of herstelcode)" : "Herstelcode", {
              name: "code", type: "text", required: true, autocomplete: "one-time-code", inputmode: "numeric",
            }),
            h("label", { class: "check" }, h("input", { type: "checkbox", name: "remember" }), "Dit apparaat 30 dagen onthouden"),
          ]
        : null,
      err,
      h("button", { class: "primary", type: "submit" }, "Aanmelden"),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "Bezig…";
      showError(err, null);
      const d = new FormData(form);
      try {
        const res = await call("vx-login", {
          server: d.get("server"),
          email: d.get("email"),
          password: d.get("password"),
          code: d.get("code") || undefined,
          remember: d.get("remember") === "on",
        });
        if (res.twoFactor) {
          if (res.securityKeyOnly) throw new Error("Je kluis vraagt een beveiligingssleutel. Gebruik je herstelcode of zet ook een authenticator-app aan.");
          render(setupView({ server: d.get("server"), email: d.get("email") }, res));
          return;
        }
        await refresh();
      } catch (ex) {
        showError(err, ex);
        btn.disabled = false;
        btn.textContent = "Aanmelden";
      }
    });
    return form;
  }

  function lockedView(status) {
    const err = errorBox();
    const form = h(
      "form",
      {},
      h("p", {}, "Kluis vergrendeld."),
      h("p", { class: "muted small" }, `${status.email} op ${status.server}`),
      field("Master password", { name: "password", type: "password", required: true, autofocus: true, autocomplete: "current-password" }),
      err,
      h("button", { class: "primary", type: "submit" }, "Ontgrendelen"),
      h("div", { class: "row" }, h("button", { type: "button", onclick: () => call("vx-logout").then(refresh) }, "Afmelden")),
    );
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      btn.textContent = "Bezig…";
      try {
        await call("vx-unlock", { password: new FormData(form).get("password") });
        await refresh();
      } catch (ex) {
        showError(err, ex);
        btn.disabled = false;
        btn.textContent = "Ontgrendelen";
      }
    });
    return form;
  }

  async function unlockedView(status) {
    const tab = await activeTab();
    let items = [];
    try {
      items = tab ? await call("vx-tab-items", { tabId: tab.id }) : [];
    } catch {
      items = [];
    }
    const err = errorBox();
    const list = items.length
      ? h(
          "ul",
          { class: "items" },
          items.map((i) =>
            h(
              "li",
              {},
              h("div", { class: "who" }, h("div", {}, i.name), h("div", { class: "muted small" }, i.username || "")),
              i.reprompt
                ? h("span", { class: "muted small", title: "Dit item vraagt het master password opnieuw" }, "via de kluis")
                : h("button", { type: "button", onclick: () => call("vx-fill-tab", { tabId: tab.id, itemId: i.id }).then(() => window.close()) }, "Invullen"),
            ),
          ),
        )
      : h("p", { class: "muted" }, "Geen login voor deze pagina (exact dezelfde host en poort als een adres in het item).");
    return h(
      "div",
      {},
      h("p", { class: "small" }, `Ontgrendeld · ${status.count} logins · gesynchroniseerd ${ago(status.syncedAt)}`),
      list,
      err,
      h(
        "div",
        { class: "row" },
        h("button", {
          type: "button",
          onclick: async (e) => {
            e.target.disabled = true;
            try {
              await call("vx-sync");
              await refresh();
            } catch (ex) {
              showError(err, ex);
              e.target.disabled = false;
            }
          },
        }, "Nu synchroniseren"),
        h("button", { type: "button", onclick: () => call("vx-lock").then(refresh) }, "Vergrendelen"),
        h("button", { type: "button", onclick: () => call("vx-logout").then(refresh) }, "Afmelden"),
      ),
    );
  }

  function render(node) {
    app.replaceChildren(node);
    const first = app.querySelector("input:not([type=checkbox]):not([value]), input[autofocus]");
    if (first) first.focus();
  }

  async function refresh() {
    const status = await call("vx-status");
    if (!status.configured) render(setupView(status));
    else if (!status.unlocked) render(lockedView(status));
    else render(await unlockedView(status));
  }

  refresh().catch((e) => render(h("p", { class: "error" }, e.message)));
})();
