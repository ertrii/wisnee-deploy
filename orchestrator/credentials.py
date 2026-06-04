"""Genera /tmp/wisnee-credentials.txt con lo necesario para el primer acceso."""

import datetime
import os
import subprocess

from . import config


def public_ip() -> str:
    for cmd in (["curl", "-s", "--max-time", "4", "ifconfig.me"],
                ["hostname", "-I"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=6).stdout.strip()
            if out:
                return out.split()[0]
        except Exception:
            pass
    return "(desconocida)"


def write(answers: dict, secrets: dict):
    domain = answers["domain"]
    ip = public_ip()
    # El wizard de bootstrap vive en la RAÍZ del SPA ("/"). Con history
    # routing (BrowserRouter) la URL es limpia, sin hash.
    setup_url = f"https://{domain}/?token={secrets['INIT_TOKEN']}"
    ts = datetime.datetime.now().isoformat(timespec="seconds")

    content = f"""WISNEE - credenciales de instalacion ({ts})
=================================================
Entorno:      {answers['env']}
Dominio:      https://{domain}
IP publica:   {ip}

PRIMER ACCESO (wizard de bootstrap):
  {setup_url}
  INIT_TOKEN: {secrets['INIT_TOKEN']}

Hosts internos (red docker 'wisnee'):
  server:     http://server:4000
  postgres:   db:5432
  wa-bridge:  http://wa-bridge:4100
  mk-bridge:  http://mk-bridge:4200  (+ UDP {answers['wg_port']} publico)

Los demas secrets (DB, SECRET_KEY, FISCAL_ENCRYPTION_KEY, secrets de los
bridges) se autogeneraron y viven en {config.ENV_DIR}/*.env (chmod 600, solo
root). A proposito no se muestran aca.

Comandos:
  ./wisnee status        ./wisnee logs [servicio]
  ./wisnee update        ./wisnee backup        ./wisnee cert

OJO: /tmp se borra al reiniciar el server. Copia este archivo a un lugar
seguro y borralo del VPS.
"""
    config.CREDENTIALS_PATH.write_text(content, encoding="utf-8")
    os.chmod(config.CREDENTIALS_PATH, 0o600)
    return config.CREDENTIALS_PATH
