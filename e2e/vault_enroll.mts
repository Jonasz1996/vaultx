// Leidt kluissleutels af met exact dezelfde code als de VaultX-webinterface
// (frontend/src/vault/crypto.ts) en print de payload voor POST /api/v1/me/vault.
//   node --experimental-strip-types e2e/vault_enroll.mts <email> <master password>
import { createVaultKeys } from "../frontend/src/vault/crypto.ts";

const [email, password] = process.argv.slice(2);
if (!email || !password) {
  console.error("gebruik: vault_enroll.mts <email> <master password>");
  process.exit(2);
}
console.log(JSON.stringify(await createVaultKeys(email, password)));
