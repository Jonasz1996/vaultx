// Connectorpagina voor de officiële Bitwarden-clients (tweede stap met een beveiligingssleutel,
// provider 7). Doet wat de connectors van de Bitwarden-webkluis doen, zodat de extensies en apps
// ook met VaultX werken:
// - webauthn-fallback-connector.html: de browserextensie opent deze pagina in een nieuw tabblad;
//   het resultaat gaat via window.postMessage naar de extensie (die het tabblad herkent).
// - webauthn-connector.html / webauthn-mobile-connector.html: de mobiele apps (terug via een
//   bitwarden://-link) en clients die de pagina in een iframe laden (postMessage naar de ouder).
(function () {
  "use strict";

  var params = new URLSearchParams(window.location.search);
  var mode = document.body.getAttribute("data-mode");
  var msg = document.getElementById("msg");
  var button = document.getElementById("webauthn-button");
  var remember = document.getElementById("remember");
  var sent = false;

  function show(text, kind) {
    msg.textContent = text;
    msg.className = kind === "error" ? "error-box" : kind === "ok" ? "ok-box" : "info-box";
    msg.hidden = false;
  }

  function b64Decode(str) {
    return decodeURIComponent(
      Array.prototype.map
        .call(atob(str), function (c) {
          return "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2);
        })
        .join(""),
    );
  }

  function fromB64url(value) {
    var b64 = value.replace(/-/g, "+").replace(/_/g, "/");
    var bin = atob(b64 + "===".slice((b64.length + 3) % 4));
    var out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function toB64url(buf) {
    var bytes = new Uint8Array(buf);
    var bin = "";
    for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  // --- parameters, zoals de Bitwarden-clients ze meegeven (v1 of v2) ---
  var webauthnJson = null;
  var callbackUri = null;
  var parentUrl = params.get("parent");
  if (parentUrl) parentUrl = decodeURIComponent(parentUrl);
  try {
    var data = params.get("data");
    if (params.get("v") === "1") {
      webauthnJson = b64Decode(data);
    } else {
      var obj = JSON.parse(b64Decode(data));
      webauthnJson = obj.data;
      var scheme = (params.get("deeplinkScheme") || "").toLowerCase();
      if (scheme === "https") callbackUri = "https://bitwarden.com/webauthn-callback";
      else if (scheme || obj.mobile === true || obj.callbackUri != null) callbackUri = "bitwarden://webauthn-callback";
    }
  } catch (e) {
    webauthnJson = null;
  }

  function parseOptions() {
    var json = typeof webauthnJson === "string" ? JSON.parse(webauthnJson) : webauthnJson;
    json.challenge = fromB64url(json.challenge);
    (json.allowCredentials || []).forEach(function (c) {
      c.id = fromB64url(c.id);
    });
    return json;
  }

  function dataString(cred) {
    return JSON.stringify({
      id: cred.id,
      rawId: toB64url(cred.rawId),
      type: cred.type,
      extensions: cred.getClientExtensionResults(),
      response: {
        authenticatorData: toB64url(cred.response.authenticatorData),
        clientDataJson: toB64url(cred.response.clientDataJSON),
        signature: toB64url(cred.response.signature),
      },
    });
  }

  function fail(text) {
    show(text, "error");
    button.disabled = false;
    if (mode !== "fallback") {
      if (callbackUri) window.location.replace(callbackUri + "?error=" + encodeURIComponent(text));
      else if (parentUrl && window.parent !== window) window.parent.postMessage("error|" + text, parentUrl);
    }
  }

  function succeed(result) {
    sent = true;
    button.disabled = true;
    if (remember) remember.disabled = true;
    if (mode === "fallback") {
      // De content script van de extensie luistert naar dit bericht op deze pagina.
      window.postMessage(
        { command: "webAuthnResult", data: result, remember: !!(remember && remember.checked) },
        "*",
      );
      show("Gelukt. De Bitwarden-extensie gaat verder; je kan dit tabblad sluiten.", "ok");
    } else if (callbackUri) {
      var uri = callbackUri + "?data=" + encodeURIComponent(result);
      show("Gelukt. Terug naar de app…", "ok");
      window.location.replace(uri);
    } else if (parentUrl) {
      window.parent.postMessage("success|" + result, parentUrl);
      show("Gelukt.", "ok");
    }
  }

  function start() {
    if (sent) return;
    if (!window.PublicKeyCredential || !navigator.credentials) {
      fail("Deze browser ondersteunt geen beveiligingssleutels.");
      return;
    }
    var options;
    try {
      options = parseOptions();
    } catch (e) {
      fail("Deze link is onvolledig. Start de login opnieuw in de Bitwarden-extensie of app.");
      return;
    }
    button.disabled = true;
    show("Steek je beveiligingssleutel in en raak hem aan als hij knippert, of gebruik je passkey.", "info");
    navigator.credentials
      .get({ publicKey: options })
      .then(function (cred) {
        succeed(dataString(cred));
      })
      .catch(function (err) {
        var name = err && err.name;
        if (name === "NotAllowedError") fail("Geannuleerd of te lang gewacht. Probeer opnieuw.");
        else if (name === "SecurityError")
          fail("Beveiligingssleutels werken enkel op de domeinnaam van VaultX met https, niet op een IP-adres.");
        else fail(String((err && err.message) || err));
      });
  }

  if (!webauthnJson) {
    fail("Deze pagina opent de Bitwarden-extensie of app zelf bij het inloggen met een beveiligingssleutel.");
    button.disabled = true;
    return;
  }
  button.addEventListener("click", start);
  // In een gewoon tabblad mag de browser meteen om de sleutel vragen; mobiel en Safari willen een klik.
  var isSafari = / Safari\//.test(navigator.userAgent) && !/Chrome/.test(navigator.userAgent);
  if (mode !== "fallback" && !callbackUri && !isSafari) start();
  if (mode !== "fallback" && parentUrl && window.parent !== window)
    window.parent.postMessage("info|ready", parentUrl);
})();
