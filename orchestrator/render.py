"""Renderiza los env/*.env, compose/.env y nginx/default.conf a partir de las
respuestas del operador + los secrets autogenerados."""

import os
from pathlib import Path

from . import config


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
        "BASE_PATH": "",
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
        "MAIL_HOST": answers.get("mail_host", ""),
        "MAIL_USER": answers.get("mail_user", ""),
        "MAIL_PASSWORD": answers.get("mail_password", ""),
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

    # No es secreto, pero lo dejamos 600 por consistencia.
    _write_env(config.COMPOSE_ENV, {
        "TAG": answers["tag"],
        "DOMAIN": domain,
        "APP_ENV": answers["env"],
        "WG_REVERSE_LISTEN_PORT": answers["wg_port"],
        # No lo usa compose; lo guardamos para el comando `cert` (recuperación).
        "CERTBOT_EMAIL": answers["email"],
    })

    render_nginx(domain)


def render_nginx(domain: str) -> None:
    """Renderiza nginx/default.conf desde el template sustituyendo solo
    ${DOMAIN} (las demás $vars son de nginx). Se llama en init y en update
    para que cambios del template se apliquen sin re-init."""
    template = config.NGINX_TEMPLATE.read_text(encoding="utf-8")
    config.NGINX_CONF.write_text(
        template.replace("${DOMAIN}", domain), encoding="utf-8"
    )
