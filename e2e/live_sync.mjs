// Luistert op de notificatie-hub van VaultX met exact de SignalR-client die de
// Bitwarden-apps gebruiken (zie bitwarden_e2e.py). Elke gebeurtenis komt als één
// JSON-regel op stdout; stdin sluiten stopt de verbinding.
//
//   VAULTX_TOKEN=<access token> node e2e/live_sync.mjs https://vaultx.example/notifications

import * as signalR from "@microsoft/signalr";
import { MessagePackHubProtocol } from "@microsoft/signalr-protocol-msgpack";

const [notificationsUrl] = process.argv.slice(2);
const token = process.env.VAULTX_TOKEN;
const out = (event) => process.stdout.write(JSON.stringify(event) + "\n");

// Zelfde opties als libs/common/.../signalr-connection.service.ts in bitwarden/clients
const connection = new signalR.HubConnectionBuilder()
  .withUrl(notificationsUrl + "/hub", {
    accessTokenFactory: () => token,
    skipNegotiation: true,
    transport: signalR.HttpTransportType.WebSockets,
  })
  .withHubProtocol(new MessagePackHubProtocol())
  .configureLogging(signalR.LogLevel.Warning)
  .build();

connection.on("ReceiveMessage", (data) => {
  out({ event: "message", data });
});
connection.onclose((error) => {
  out({ event: "closed", error: error ? String(error.message ?? error) : null });
  process.exit(0);
});

try {
  await connection.start();
  out({ event: "connected" });
} catch (error) {
  out({ event: "failed", error: String(error.message ?? error) });
  process.exit(1);
}

process.stdin.on("end", () => connection.stop());
process.stdin.resume();
