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
