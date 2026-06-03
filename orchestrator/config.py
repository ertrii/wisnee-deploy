"""Rutas y constantes del orquestador. Todo es relativo a la raíz del repo
(que en el VPS vive en /opt/wisnee tras clonar)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANSIBLE_DIR = REPO / "ansible"
COMPOSE_DIR = REPO / "compose"
ENV_DIR = REPO / "env"
NGINX_DIR = REPO / "nginx"
BACKUPS_DIR = REPO / "backups"

COMPOSE_BASE = COMPOSE_DIR / "docker-compose.yml"
COMPOSE_PROD = COMPOSE_DIR / "docker-compose.prod.yml"
COMPOSE_DEMO = COMPOSE_DIR / "docker-compose.demo.yml"
COMPOSE_ENV = COMPOSE_DIR / ".env"
NGINX_TEMPLATE = NGINX_DIR / "default.conf.template"
NGINX_CONF = NGINX_DIR / "default.conf"

# El nombre de proyecto lo fija `name: wisnee` en los compose → los volúmenes
# quedan como `wisnee_<vol>`.
PROJECT = "wisnee"
CERTBOT_CONF_VOLUME = f"{PROJECT}_certbot-conf"

CREDENTIALS_PATH = Path("/tmp/wisnee-credentials.txt")

DB_USER = "wisnee"
DB_NAME = "wisnee"
SERVER_PORT = "4000"


def read_env_file(path: Path) -> dict:
    """Parser mínimo de KEY=VALUE (ignora comentarios y líneas vacías)."""
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def write_env_file(path: Path, data: dict, mode: int = 0o600) -> None:
    """Escribe KEY=VALUE (sin comentarios). Usado para regrabar compose/.env."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{k}={v}" for k, v in data.items()) + "\n"
    path.write_text(body, encoding="utf-8")
    os.chmod(path, mode)
