// Master password wijzigen met exact dezelfde code als de VaultX-webinterface
// (frontend/src/vault/crypto.ts). Leest JSON van stdin, print JSON:
//   derive: {params, password}                                   -> {loginHash, masterKey}
//   change: {masterKey, loginHash, encUserKey, salt, newPassword, next} -> payload voor /me/vault/password
//   node --experimental-strip-types e2e/vault_password.mts derive|change < input.json
import { changeMasterPassword, deriveLogin } from "../frontend/src/vault/crypto.ts";

const mode = process.argv[2];
const input = JSON.parse(await new Response(process.stdin as unknown as ReadableStream).text());
const b64 = (b: Uint8Array) => Buffer.from(b).toString("base64");
const unb64 = (s: string) => new Uint8Array(Buffer.from(s, "base64"));

if (mode === "derive") {
  const { masterKey, loginHash } = await deriveLogin(input.params, input.password);
  console.log(JSON.stringify({ loginHash, masterKey: b64(masterKey) }));
} else if (mode === "change") {
  const current = { masterKey: unb64(input.masterKey), loginHash: input.loginHash };
  console.log(
    JSON.stringify(await changeMasterPassword(current, input.encUserKey, input.salt, input.newPassword, input.next)),
  );
} else {
  console.error("gebruik: vault_password.mts derive|change < input.json");
  process.exit(2);
}
