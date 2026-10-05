# End-to-end test met echte Authentik

Deze test logt in een echte browser in via een echte Authentik-instantie en
controleert login, groepmapping, organisatie- en teambeheer, de auditketen,
uitloggen (RP-initiated logout) en back-channel logout.

```bash
# 1. Authentik met de VaultX-blueprint (alice = beheerder, bob = lid)
docker compose -f docker/authentik/compose.yml up -d
#    wacht tot http://localhost:9000/application/o/vaultx/.well-known/openid-configuration 200 geeft

# 2. VaultX-backend en UI op http://localhost:8080, met deze instellingen:
#    VAULTX_PUBLIC_URL=http://localhost:8080
#    VAULTX_OIDC_ISSUER=http://localhost:9000/application/o/vaultx/
#    VAULTX_OIDC_CLIENT_ID=vaultx-dev
#    VAULTX_OIDC_CLIENT_SECRET=vaultx-dev-client-secret-0123456789
#    VAULTX_COOKIE_SECURE=false
#    De backend moet op poort 8000 van de host luisteren (back-channel logout vanuit Authentik).
#    Bijvoorbeeld: backend met uvicorn op de host en `npm run dev -- --port 8080` voor de UI.

# 3. Test draaien (lege VaultX-database)
pip install playwright && playwright install chromium
python e2e/authentik_e2e.py ./e2e-shots
```

# End-to-end test met echte Nginx Proxy Manager

`npm_e2e.py` maakt in NPM een reeks proxy hosts onder `.vaultx-e2e.test` aan
(officieel Authentik-patroon, access list met "Satisfy Any", labels, een host
met een nginx-fout) en controleert wat de VaultX-connector eruit afleidt. Op
een verse NPM maakt het script eerst de eerste gebruiker aan. CI draait dit
tegen NPM 2.16.0.

```bash
docker run -d --name npm -p 81:81 -v npmdata:/data -v npmle:/etc/letsencrypt \
  docker.io/jc21/nginx-proxy-manager:2.16.0
cd backend && python ../e2e/npm_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord

# Enkel demo-hosts zetten (bv. om de UI te bekijken), zonder controle:
python ../e2e/npm_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord --seed-only
```

`npm_write_e2e.py` test het zetten van Authentik-bescherming via VaultX. Het
start een nep-outpost (die ook de applicatie achter NPM speelt), een
nep-Authentik (OIDC) en de VaultX-backend, maakt hosts aan onder
`.vaultx-write-e2e.test` (HTTP, HTTPS met eigen certificaat, een extra custom
location, een access list op "Satisfy Any") en controleert via NPM's
proxypoorten: bescherming zetten (zonder sessie naar Authentik, met sessie de
app met `X-authentik-*`-headers), weghalen (config weer zoals ervoor), een
outpost die nginx niet kan resolven en een outpost die 500 geeft (allebei
teruggezet) en een geweigerde host. NPM moet de app op de Docker-host kunnen
bereiken (`--docker-host`, standaard `172.17.0.1`).

```bash
docker run -d --name npm -p 81:81 -p 8080:80 -p 8443:443 \
  -v npmdata:/data -v npmle:/etc/letsencrypt docker.io/jc21/nginx-proxy-manager:2.16.0
# (op een machine zonder IPv6: -e DISABLE_IPV6=true)
cd backend
python ../e2e/npm_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord   # maakt de NPM-gebruiker aan
VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \
  python ../e2e/npm_write_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord \
  --proxy 127.0.0.1 --http-port 8080 --https-port 8443
```

# End-to-end test fase 4: echte Authentik én echte NPM

`authentik_npm_e2e.py` test "Beschermen met Authentik" in één stap. Het maakt
met het beheertoken van Authentik een serviceaccount met enkel de rechten uit
docs/npm.md (sectie 6) en start VaultX met dát token. Dan: outposts opvragen,
een host beschermen met toegang voor de organisatie (VaultX maakt provider,
applicatie, groepsbinding en outpost-toewijzing aan en controleert met de
echte ingebouwde outpost), met `--browser` aanmelden in Chromium (alice, lid
van `vaultx:<org>`, komt bij de app; eve wordt door Authentik geweigerd), een
host met toegang voor iedereen, weghalen (Authentik-objecten weg) en een
outpost-URL die niet antwoordt (NPM en Authentik teruggedraaid).

NPM moet poort 80 aanbieden (de browser gebruikt gewone http-URL's) en
Authentik bereiken op `--outpost-url` (standaard `http://172.17.0.1:9000`).

```bash
# Authentik met een vast beheertoken (AUTHENTIK_BOOTSTRAP_TOKEN), bv. via docker/authentik/compose.yml
docker run -d --name npm -p 81:81 -p 80:80 -v npmdata:/data -v npmle:/etc/letsencrypt \
  docker.io/jc21/nginx-proxy-manager:2.16.0
cd backend
python ../e2e/npm_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord   # maakt de NPM-gebruiker aan
pip install playwright==1.56.0 && playwright install chromium
VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \
  python ../e2e/authentik_npm_e2e.py http://localhost:81 admin@example.com een-lang-wachtwoord \
  --authentik http://localhost:9000 --authentik-token <beheertoken> --browser
```

# End-to-end test met de officiële Bitwarden CLI

`bitwarden_e2e.py` start zelf een nep-Authentik (OIDC) en de VaultX-backend met
een zelfondertekend TLS-certificaat (de clients weigeren `http://`). Het
activeert een kluis met exact de browsercode van de webinterface
(`frontend/src/vault/crypto.ts`, via Node) en doet daarna met `bw`: login, sync,
map en items aanmaken, wijzigen, lock/unlock, prullenbak, terugzetten en
definitief verwijderen. Tot slot wordt de gebruiker gedeactiveerd en moeten
sync en login falen. CI draait dit met CLI 2026.9.1.

```bash
npm install -g @bitwarden/cli@2026.9.1          # of BW=/pad/naar/bw
cd backend
VAULTX_DATABASE_URL=postgresql+psycopg://vaultx:vaultx@localhost:5432/vaultx_e2e \
  python ../e2e/bitwarden_e2e.py
# E2E_SERVER_LOG=info toont elke request; handig om te zien welke endpoints een nieuwe client verwacht.
```

Vereist Node 22 of nieuwer en een lege database.
