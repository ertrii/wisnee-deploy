"""Wrappers de subprocess: Ansible, Docker, compose, certbot."""

import json
import subprocess

from . import config

CYAN = "\033[36m"
RESET = "\033[0m"


def run(cmd, input_text=None, cwd=None, check=True):
    print(f"\n{CYAN}+ {' '.join(cmd)}{RESET}", flush=True)
    return subprocess.run(cmd, input=input_text, text=True, cwd=cwd, check=check)


# ---- Docker / compose ----

def compose_argv(env_name: str):
    files = ["-f", str(config.COMPOSE_BASE)]
    if env_name == "prod":
        files += ["-f", str(config.COMPOSE_PROD)]
    elif env_name == "demo":
        files += ["-f", str(config.COMPOSE_DEMO)]
    return ["docker", "compose", "--env-file", str(config.COMPOSE_ENV)] + files


def compose(args, env_name: str, check=True):
    return run(compose_argv(env_name) + args, check=check)


def docker_login(user: str, token: str):
    run(["docker", "login", "ghcr.io", "-u", user, "--password-stdin"],
        input_text=token)


# ---- Ansible ----

def ensure_ansible():
    if subprocess.run(["which", "ansible-playbook"],
                      capture_output=True).returncode == 0:
        return
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "ansible"])


def ansible_provision(extra_vars: dict):
    run(["ansible-galaxy", "collection", "install", "-r", "requirements.yml"],
        cwd=str(config.ANSIBLE_DIR))
    run(["ansible-playbook", "playbook.yml",
         "--extra-vars", json.dumps(extra_vars)],
        cwd=str(config.ANSIBLE_DIR))


# ---- TLS (certbot) ----

def _certbot_conf_run(script: str):
    """Corre un sh dentro del volumen de certbot (para el dummy cert / limpieza)."""
    run(["docker", "run", "--rm", "--entrypoint", "sh",
         "-v", f"{config.CERTBOT_CONF_VOLUME}:/etc/letsencrypt",
         "alpine/openssl", "-c", script])


def dummy_cert(domain: str):
    """Cert self-signed temporal para que nginx pueda arrancar antes de tener
    el real. No pisa un cert ya existente."""
    live = f"/etc/letsencrypt/live/{domain}"
    _certbot_conf_run(
        f"mkdir -p {live} && [ -f {live}/fullchain.pem ] || "
        f"openssl req -x509 -nodes -newkey rsa:2048 -days 1 "
        f"-keyout {live}/privkey.pem -out {live}/fullchain.pem -subj /CN={domain}"
    )


def certbot_issue(domain: str, email: str, env_name: str):
    """Emite el cert real. Antes borra el dummy para que certbot cree su propio
    lineage limpio en live/<domain> (si no, lo crearía en live/<domain>-0001)."""
    _certbot_conf_run(
        f"rm -rf /etc/letsencrypt/live/{domain} "
        f"/etc/letsencrypt/archive/{domain} "
        f"/etc/letsencrypt/renewal/{domain}.conf"
    )
    compose(["run", "--rm", "--entrypoint", "certbot", "certbot",
             "certonly", "--webroot", "-w", "/var/www/certbot",
             "-d", domain, "--email", email, "--agree-tos", "-n"], env_name)


def nginx_reload(env_name: str):
    compose(["exec", "proxy", "nginx", "-s", "reload"], env_name)
