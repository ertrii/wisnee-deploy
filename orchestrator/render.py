"""Renderiza los env/*.env, compose/.env y nginx/default.conf a partir de las
respuestas del operador + los secrets autogenerados."""

import os
from pathlib import Path

from . import config, secretgen


def _write_env(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={v}" for k, v in data.items()) + "\n"
    path.write_text(body, encoding="utf-8")
    os.chmod(path, mode)


def render(answers: dict, secrets: dict) -> None:
    domain = answers["domain"]
    base_url = f"https://{domain}"

    _write_env(config.ENV_DIR / "db.env", {
        "POSTGRES_USER": config.DB_USER,
        "POSTGRES_PASSWORD": secrets["DB_PASSWORD"],
        "POSTGRES_DB": config.DB_NAME,
    })

    _write_env(config.ENV_DIR / "server.env", {
        "SERVER_PORT": config.SERVER_PORT,
        "HTTPS": "true",
        "PUBLIC_BASE_URL": base_url,
        "CORS_ORIGIN": base_url,
        "DB_HOST": "db",
        "DB_PORT": "5432",
        "DB_USERNAME": config.DB_USER,
        "DB_PASSWORD": secrets["DB_PASSWORD"],
        "DB_NAME": config.DB_NAME,
        "SECRET_KEY": secrets["SECRET_KEY"],
        "INIT_TOKEN": secrets["INIT_TOKEN"],
        "FISCAL_ENCRYPTION_KEY": secrets["FISCAL_ENCRYPTION_KEY"],
        "WA_BRIDGE_URL": "http://wa-bridge:4100",
        "WA_BRIDGE_SECRET": secrets["WA_BRIDGE_SECRET"],
        "MK_BRIDGE_URL": "http://mk-bridge:4200",
        "MK_BRIDGE_SECRET": secrets["MK_BRIDGE_SECRET"],
        "VPN_HUB_URL": "http://vpn-hub:4300",
        "VPN_HUB_SECRET": secrets["VPN_HUB_SECRET"],
    })

    _write_env(config.ENV_DIR / "wa-bridge.env", {
        "PORT": "4100",
        "INTERNAL_SECRET": secrets["WA_BRIDGE_SECRET"],
        "WEBHOOK_URL": "http://server:4000/api/whatsapp/webhook/message",
    })

    _write_env(config.ENV_DIR / "mk-bridge.env", {
        "PORT": "4200",
        "INTERNAL_SECRET": secrets["MK_BRIDGE_SECRET"],
        "DRY": "false",
        "WG_CONFIG_DIR": "/etc/wireguard",
        "WG_REVERSE_LISTEN_PORT": answers["wg_port"],
        "WG_REVERSE_PUBLIC_ENDPOINT": answers["wg_endpoint"],
    })

    # vpn-hub: concentrador SSTP (Mikrotiks bajo CGNAT con RouterOS 6). El
    # SSTP escucha en 1443 (el 443 lo usa el proxy). SSTP_PUBLIC_HOST es el
    # dominio: resuelve al mismo VPS, y el sstp-client del Mikrotik se conecta
    # a <dominio>:1443. El pool de gestión usa el default del servicio.
    _write_env(config.ENV_DIR / "vpn-hub.env", {
        "PORT": "4300",
        "INTERNAL_SECRET": secrets["VPN_HUB_SECRET"],
        "DRY": "false",
        "SSTP_PUBLIC_HOST": domain,
        "SSTP_LISTEN_PORT": "1443",
    })

    # No es secreto, pero lo dejamos 600 por consistencia.
    _write_env(config.COMPOSE_ENV, {
        "TAG": answers["tag"],
        "DOMAIN": domain,
        "APP_ENV": answers["env"],
        "WG_REVERSE_LISTEN_PORT": answers["wg_port"],
        # Puerto TCP público del SSTP (el 443 lo usa el proxy). El compose lo
        # publica como ${SSTP_LISTEN_PORT:-1443}.
        "SSTP_LISTEN_PORT": "1443",
        # No lo usa compose; lo guardamos para el comando `cert` (recuperación).
        "CERTBOT_EMAIL": answers["email"],
    })

    render_nginx(domain)


def reconcile_service_envs() -> list:
    """Completa los env/*.env de los servicios bridge que falten y agrega en
    server.env los *_URL/*_SECRET que falten, generando SOLO los secrets
    ausentes y SIN tocar los existentes (preserva DB_PASSWORD, SECRET_KEY, etc.,
    o sea: ni la BD ni las sesiones del cliente se rompen).

    Pensado para droplets instalados antes de que el stack incorporara mk-bridge
    o vpn-hub: ahí el `git pull` trae un compose que ya referencia
    env/vpn-hub.env, pero ese archivo (y su secret) nunca se generó y el
    `up -d` aborta con 'env file ... not found'. Idempotente: en un droplet al
    día no escribe nada. Devuelve la lista de archivos creados/actualizados.
    """
    created = []
    server_path = config.ENV_DIR / "server.env"
    server = config.read_env_file(server_path)
    if not server:
        return created  # sin configurar; cmd_update ya valida antes que llegue acá

    compose = config.read_env_file(config.COMPOSE_ENV)
    domain = compose.get("DOMAIN", "")
    wg_port = compose.get("WG_REVERSE_LISTEN_PORT", "51820")
    server_changed = False

    def ensure_secret(key):
        nonlocal server_changed
        if not server.get(key):
            server[key] = secretgen.token(32)
            server_changed = True
        return server[key]

    def ensure_url(key, value):
        nonlocal server_changed
        if not server.get(key):
            server[key] = value
            server_changed = True

    # wa-bridge (suele existir; se respeta si ya está)
    wa_secret = ensure_secret("WA_BRIDGE_SECRET")
    ensure_url("WA_BRIDGE_URL", "http://wa-bridge:4100")
    wa_env = config.ENV_DIR / "wa-bridge.env"
    if not wa_env.exists():
        _write_env(wa_env, {
            "PORT": "4100",
            "INTERNAL_SECRET": wa_secret,
            "WEBHOOK_URL": "http://server:4000/api/whatsapp/webhook/message",
        })
        created.append("wa-bridge.env")

    # mk-bridge
    mk_secret = ensure_secret("MK_BRIDGE_SECRET")
    ensure_url("MK_BRIDGE_URL", "http://mk-bridge:4200")
    mk_env = config.ENV_DIR / "mk-bridge.env"
    if not mk_env.exists():
        _write_env(mk_env, {
            "PORT": "4200",
            "INTERNAL_SECRET": mk_secret,
            "DRY": "false",
            "WG_CONFIG_DIR": "/etc/wireguard",
            "WG_REVERSE_LISTEN_PORT": wg_port,
            "WG_REVERSE_PUBLIC_ENDPOINT": f"{domain}:{wg_port}" if domain else "",
        })
        created.append("mk-bridge.env")

    # vpn-hub
    vpn_secret = ensure_secret("VPN_HUB_SECRET")
    ensure_url("VPN_HUB_URL", "http://vpn-hub:4300")
    vpn_env = config.ENV_DIR / "vpn-hub.env"
    if not vpn_env.exists():
        _write_env(vpn_env, {
            "PORT": "4300",
            "INTERNAL_SECRET": vpn_secret,
            "DRY": "false",
            "SSTP_PUBLIC_HOST": domain,
            "SSTP_LISTEN_PORT": "1443",
        })
        created.append("vpn-hub.env")

    if server_changed:
        config.write_env_file(server_path, server)
        created.append("server.env")

    return created


def render_nginx(domain: str) -> None:
    """Renderiza nginx/default.conf desde el template sustituyendo solo
    ${DOMAIN} (las demás $vars son de nginx). Se llama en init y en update
    para que cambios del template se apliquen sin re-init."""
    template = config.NGINX_TEMPLATE.read_text(encoding="utf-8")
    config.NGINX_CONF.write_text(
        template.replace("${DOMAIN}", domain), encoding="utf-8"
    )
