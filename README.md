# wisnee-deploy

Orquestación del stack Wisnee en un VPS (Ubuntu Server 24.04+). Las imágenes se
bajan de **GHCR** (no se buildea en el server). Dos entornos: **prod** y **demo**
(este último con seed). Un CLI en **Python** (`./wisnee`) + **Ansible** generan
los `.env` con secrets autogenerados y manejan el sistema; **docker compose** es
la capa de runtime.

## Instalación rápida (en el VPS)

```bash
# 1. Crear el droplet con tu llave SSH (Ubuntu 24.04). Recomendado: 4 GB para prod.
# 2. Apuntar el DNS del dominio a la IP del droplet.
# 3. En el server (como root):
git clone https://github.com/ertrii/wisnee-deploy.git /opt/wisnee
cd /opt/wisnee
sudo ./wisnee init        # pregunta dominio, email, GHCR, etc. y levanta todo
```

`init` hace: prompts → **autogenera todos los secrets** → render de `env/*` y
nginx → Ansible (docker, swap, UFW, fail2ban, SSH key-only) → `docker login` +
`pull` → migrate → `up` → emite el certificado TLS → escribe
`/tmp/wisnee-credentials.txt` (con la URL de setup + `INIT_TOKEN`).

## Comandos

```bash
sudo ./wisnee init [--force] [--skip-provision] [--no-harden]
./wisnee update        # baja imágenes nuevas, migra y recrea (actualizaciones)
./wisnee status        # docker compose ps
./wisnee logs [svc]    # logs en vivo
./wisnee seed          # (solo Demo) resetea la BD y siembra datos
./wisnee cert          # emite/renueva el certificado (recuperación)
./wisnee backup        # pg_dump → backups/
./wisnee credentials   # muestra el credentials.txt
```

> Requiere `python3` (viene en Ubuntu) y, para `init`, permisos de root.

## Estructura

```
compose/
  docker-compose.yml        base: db, migrate(one-shot), server, web(SPA), proxy(nginx), certbot
  docker-compose.prod.yml   overlay: + wa-bridge, mk-bridge
  docker-compose.demo.yml   overlay: + seed(one-shot, APP_ENV=demo)
  .env.example              variables de compose (TAG, DOMAIN, APP_ENV, WG port)
nginx/
  default.conf.template     reverse proxy (render ${DOMAIN}); same-origin SPA + /api + WS
env/
  db.env / server.env / wa-bridge.env / mk-bridge.env   (los genera el orquestador)
```

Solo el `proxy` se publica (80/443). `mk-bridge` además expone el UDP del WG
reverso. Postgres y los bridges quedan en la red interna `wisnee`.

## Secrets (autogenerados, deben coincidir)

- `db.env:POSTGRES_*` ↔ `server.env:DB_*`
- `wa-bridge.env:INTERNAL_SECRET` ↔ `server.env:WA_BRIDGE_SECRET`
- `mk-bridge.env:INTERNAL_SECRET` ↔ `server.env:MK_BRIDGE_SECRET`
- `server.env`: `SECRET_KEY`, `INIT_TOKEN`, `FISCAL_ENCRYPTION_KEY` (nadie los escribe a mano)

## Levantar

```bash
# render del nginx (solo $DOMAIN):
envsubst '$DOMAIN' < nginx/default.conf.template > nginx/default.conf

# Producción
docker compose -f compose/docker-compose.yml -f compose/docker-compose.prod.yml up -d

# Demo (core-only + seed)
APP_ENV=demo docker compose -f compose/docker-compose.yml -f compose/docker-compose.demo.yml up -d
```

`migrate` corre las migraciones y termina; `server` espera a que complete. El
seed **resetea la BD** y solo corre con `APP_ENV=demo` (el contenedor aborta si no).

## TLS (bootstrap del certificado)

nginx no arranca si el `ssl_certificate` no existe → se evita el huevo-y-gallina
con un cert dummy y luego se reemplaza por el real (lo automatiza el orquestador):

```bash
DOMAIN=tu-dominio.com
# 1) cert self-signed temporal para que nginx levante
docker run --rm -v wisnee_certbot-conf:/etc/letsencrypt alpine/openssl req -x509 -nodes \
  -days 1 -newkey rsa:2048 \
  -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
  -out   /etc/letsencrypt/live/$DOMAIN/fullchain.pem -subj "/CN=$DOMAIN"
# 2) up del proxy → 3) emitir el real por webroot:
docker compose ... run --rm certbot certonly --webroot -w /var/www/certbot -d $DOMAIN --email TU_EMAIL --agree-tos -n
# 4) recargar nginx:
docker compose ... exec proxy nginx -s reload
```

El servicio `certbot` del compose renueva en loop cada 12 h.

## Actualizar

```bash
./wisnee update                       # baja imágenes nuevas (tags actuales), migra y recrea
./wisnee update --tag v2.0.0          # mueve TODOS los servicios al tag v2.0.0
./wisnee update --web v2.0.0-beta.2   # mueve SOLO el frontend (server/bridges quedan igual)
```

Cada servicio puede pinearse por separado (`--server`, `--web`, `--wa`, `--mk`)
o todos juntos con `--tag`. Los overrides se persisten en `compose/.env`
(`SERVER_TAG`, `WEB_TAG`, `WA_TAG`, `MK_TAG`); si no se setean, caen al `TAG`
global. Esto permite subir un cambio que tocó solo el frontend sin recompilar
ni recrear el server. Como cada repo publica su imagen por su propio tag git
(`v*.*.*`), basta con cortar el tag en el repo que cambió y apuntar ahí.

Manual equivalente:

```bash
docker compose ... pull          # baja las imágenes de los tags vigentes
docker compose ... up -d         # recrea solo lo cambiado (migrate corre antes del server)
docker image prune -f
```
