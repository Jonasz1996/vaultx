// Unittests voor de bibliotheken van de extensie: node --test extension/test/
const test = require("node:test");
const assert = require("node:assert/strict");
const { uriMatches, matchingItems } = require("../lib/match.js");
const { totpCode, parseTotp } = require("../lib/totp.js");
const bw = require("../lib/bw.js");
globalThis.hashwasm = require("../lib/vendor/argon2.umd.min.js");

test("enkel exact dezelfde host en poort", () => {
  const item = "https://login.example.com";
  assert.ok(uriMatches(item, null, "https://login.example.com/sign-in?x=1"));
  assert.ok(uriMatches(item, null, "https://LOGIN.example.com./"));
  assert.ok(!uriMatches(item, null, "https://evil.example.com/"));
  assert.ok(!uriMatches(item, null, "https://example.com/"));
  assert.ok(!uriMatches(item, null, "https://login.example.com.evil.test/"));
  assert.ok(!uriMatches(item, null, "https://login.example.com:8443/"));
  assert.ok(uriMatches("https://login.example.com:443/x", null, "https://login.example.com/"));
  assert.ok(uriMatches("http://app.lan:8080", null, "http://app.lan:8080/login"));
  assert.ok(!uriMatches("http://app.lan:8080", null, "http://app.lan:8081/login"));
});

test("geen http als het item https heeft; zonder schema geldt https", () => {
  assert.ok(!uriMatches("https://app.lan", null, "http://app.lan/"));
  assert.ok(uriMatches("http://app.lan", null, "https://app.lan/"));
  assert.ok(uriMatches("app.lan", null, "https://app.lan/"));
  assert.ok(!uriMatches("app.lan", null, "http://app.lan/"));
  assert.ok(!uriMatches("javascript:alert(1)", null, "https://app.lan/"));
});

test("Bitwarden-matchtypes", () => {
  assert.ok(!uriMatches("https://a.lan", 5, "https://a.lan/"));
  assert.ok(uriMatches("https://a.lan/admin", 2, "https://a.lan/admin/login"));
  assert.ok(!uriMatches("https://a.lan/admin", 2, "https://a.lan/other"));
  assert.ok(uriMatches("https://a.lan/x", 3, "https://a.lan/x"));
  assert.ok(!uriMatches("https://a.lan/x", 3, "https://a.lan/x?y"));
  // Een reguliere expressie geldt enkel samen met dezelfde host.
  assert.ok(!uriMatches("^https://.*$", 4, "https://b.lan/"));
  // Domein (0) is bij ons gewoon dezelfde host.
  assert.ok(!uriMatches("https://a.lan", 0, "https://sub.a.lan/"));
  const items = [{ id: 1, uris: [{ uri: "https://a.lan" }] }, { id: 2, uris: [{ uri: "https://b.lan" }] }];
  assert.deepEqual(matchingItems(items, "https://b.lan/login").map((i) => i.id), [2]);
});

test("TOTP volgens RFC 6238 (SHA1, SHA256, SHA512)", async () => {
  const s1 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"; // "12345678901234567890"
  const s256 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZA";
  const s512 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQGEZDGNA";
  const cases = [
    [59, "94287082", "46119246", "90693936"],
    [1111111109, "07081804", "68084774", "25091201"],
    [20000000000, "65353130", "77737706", "47863826"],
  ];
  for (const [t, c1, c256, c512] of cases) {
    const now = t * 1000;
    assert.equal((await totpCode(`otpauth://totp/x?secret=${s1}&digits=8`, now)).code, c1);
    assert.equal((await totpCode(`otpauth://totp/x?secret=${s256}&digits=8&algorithm=SHA256`, now)).code, c256);
    assert.equal((await totpCode(`otpauth://totp/x?secret=${s512}&digits=8&algorithm=SHA512`, now)).code, c512);
  }
  assert.equal((await totpCode("JBSWY3DPEHPK3PXP", 0)).code.length, 6);
  assert.equal(parseTotp("otpauth://hotp/x?secret=JBSWY3DPEHPK3PXP"), null);
  assert.equal(parseTotp("steam://ABC"), null);
  assert.equal(parseTotp("geen geheim!"), null);
});

test("kluis openen in het Bitwarden-formaat (PBKDF2 en Argon2id)", async () => {
  const userKey = crypto.getRandomValues(new Uint8Array(64));
  for (const params of [
    { kdf: 0, iterations: 600000, salt: "jonas@example.com" },
    { kdf: 1, iterations: 3, memory: 64, parallelism: 4, salt: "jonas@example.com" },
  ]) {
    const mkey = await bw.masterKey(params, "correct horse");
    const encUserKey = await bw.encryptBytes(userKey, await bw.stretch(mkey));
    assert.deepEqual(await bw.openUserKey(mkey, encUserKey), userKey);
    const wrong = await bw.masterKey(params, "fout");
    await assert.rejects(bw.openUserKey(wrong, encUserKey));
  }
  await assert.rejects(bw.masterKey({ kdf: 0, iterations: 1, salt: "x" }, "pw"), /te weinig/);
  await assert.rejects(bw.masterKey({ kdf: 1, iterations: 1, memory: 1, parallelism: 1, salt: "x" }, "pw"), /te zwakke/);
});

test("login-item met itemsleutel en vx-velden", async () => {
  const uk = crypto.getRandomValues(new Uint8Array(64));
  const ik = crypto.getRandomValues(new Uint8Array(64));
  const e = (s) => bw.encryptBytes(new TextEncoder().encode(s), ik);
  const raw = {
    id: "c1", type: 1, key: await bw.encryptBytes(ik, uk), name: await e("Grafana"), reprompt: 0,
    login: { username: await e("admin"), password: await e("geheim"), totp: await e("JBSWY3DPEHPK3PXP"), uris: [{ uri: await e("https://grafana.lan"), match: null }] },
    fields: [{ name: await e("vx:enter"), value: await e("Nee"), type: 0 }],
  };
  const item = await bw.decryptLogin(raw, uk);
  assert.equal(item.name, "Grafana");
  assert.equal(item.password, "geheim");
  assert.equal(item.uris[0].uri, "https://grafana.lan");
  assert.equal(item.submit, false);
  assert.equal(item.autofill, true);
  assert.equal(await bw.decryptLogin({ ...raw, type: 2 }, uk), null);
  assert.equal(await bw.decryptLogin({ ...raw, deletedDate: "2026-01-01" }, uk), null);
});
