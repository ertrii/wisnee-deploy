"""CLI del orquestador. Comandos: init, update, seed, logs, status, backup,
cert, credentials."""

import argparse
import datetime
import subprocess
import sys

from . import config, credentials, prompts, render, runner, secretgen


def _die(msg: str, code: int = 1):
    print(f"\n✖ {msg}", file=sys.stderr)
    sys.exit(code)


def _require_configured() -> dict:
    cfg = config.read_env_file(config.COMPOSE_ENV)
    if not cfg or not (config.ENV_DIR / "server.env").exists():
        _die("No está configurado todavía. Corré primero: sudo ./wisnee init")
    return cfg


# ---- comandos ----

def cmd_init(args):
    if (config.ENV_DIR / "server.env").exists() and not args.force:
        _die("Ya está configurado (existe env/server.env). Para actualizar usá "
             "`./wisnee update`. Para reconfigurar desde cero, `init --force` "
             "(¡regenera secrets y rompe la BD existente!).")

    answers = prompts.ask_init()
    secrets = secretgen.generate()

    print("\n→ Generando configuración y secrets…")
    render.render(answers, secrets)

    if not args.skip_provision:
        print("\n→ Provisioning del sistema (Ansible)…")
        runner.ensure_ansible()
        runner.ansible_provision({
            "ghcr_username": answers["ghcr_user"],
            "ghcr_token": answers["ghcr_token"],
            "harden_ssh": not args.no_harden,
        })

    if answers["ghcr_token"]:
        try:
            runner.docker_login(answers["ghcr_user"], answers["ghcr_token"])
        except subprocess.CalledProcessError:
            _die(
                "Falló el login a GHCR. El token debe ser un PAT *classic* de "
                f"GitHub con scope read:packages, sin vencer, del usuario "
                f"'{answers['ghcr_user']}'. Probalo a mano:\n"
                f"  docker login ghcr.io -u {answers['ghcr_user']}\n"
                "Luego reintentá: ./wisnee init --force"
            )

    env = answers["env"]
    print("\n→ Bajando imágenes de GHCR…")
    runner.compose(["pull"], env)

    print("\n→ Levantando el stack…")
    runner.dummy_cert(answers["domain"])
    runner.compose(["up", "-d"], env)

    print("\n→ Emitiendo certificado TLS…")
    try:
        runner.certbot_issue(answers["domain"], answers["email"], env)
        runner.nginx_reload(env)
    except subprocess.CalledProcessError as e:
        print(f"\n⚠ No se pudo emitir el certificado ({e}).")
        print(f"  Verificá que el DNS de {answers['domain']} apunte a este "
              f"server y reintentá: ./wisnee cert")
        runner.dummy_cert(answers["domain"])  # que nginx siga arriba

    path = credentials.write(answers, secrets)
    print(f"\n✔ Listo. Credenciales en: {path}")
    print(f"  Setup:  https://{answers['domain']}/#/setup?token={secrets['INIT_TOKEN']}")


def cmd_update(args):
    cfg = _require_configured()
    env = cfg.get("APP_ENV", "prod")
    print("→ Bajando imágenes nuevas…")
    runner.compose(["pull"], env)
    print("→ Aplicando (migrate one-shot corre antes del server)…")
    runner.compose(["up", "-d"], env)
    print("✔ Actualizado.")


def cmd_seed(args):
    cfg = _require_configured()
    env = cfg.get("APP_ENV", "")
    if env != "demo":
        _die("El seed solo corre en entorno Demo (APP_ENV=demo). Resetea la BD.")
    if not args.yes:
        ans = input("Esto BORRA la base y siembra datos demo. ¿Seguir? (escribí 'demo'): ")
        if ans.strip() != "demo":
            _die("Cancelado.")
    runner.compose(["run", "--rm", "seed"], env)
    print("✔ Seed aplicado.")


def cmd_logs(args):
    cfg = _require_configured()
    env = cfg.get("APP_ENV", "prod")
    runner.compose(["logs", "-f", "--tail", "200"] + args.service, env)


def cmd_status(args):
    cfg = _require_configured()
    runner.compose(["ps"], cfg.get("APP_ENV", "prod"))


def cmd_cert(args):
    cfg = _require_configured()
    domain = cfg.get("DOMAIN")
    email = cfg.get("CERTBOT_EMAIL", "")
    env = cfg.get("APP_ENV", "prod")
    runner.dummy_cert(domain)
    runner.compose(["up", "-d", "proxy"], env)
    runner.certbot_issue(domain, email, env)
    runner.nginx_reload(env)
    print("✔ Certificado emitido/renovado.")


def cmd_backup(args):
    cfg = _require_configured()
    env = cfg.get("APP_ENV", "prod")
    db = config.read_env_file(config.ENV_DIR / "db.env")
    user = db.get("POSTGRES_USER", config.DB_USER)
    name = db.get("POSTGRES_DB", config.DB_NAME)
    config.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = config.BACKUPS_DIR / f"wisnee-{ts}.sql"
    argv = runner.compose_argv(env) + ["exec", "-T", "db", "pg_dump", "-U", user, name]
    print(f"→ pg_dump → {out}")
    with open(out, "wb") as fh:
        subprocess.run(argv, stdout=fh, check=True)
    print(f"✔ Backup: {out}")


def cmd_credentials(args):
    if config.CREDENTIALS_PATH.exists():
        print(config.CREDENTIALS_PATH.read_text(encoding="utf-8"))
    else:
        _die(f"No existe {config.CREDENTIALS_PATH} (¿se reinició el server? /tmp se borra).")


def build_parser():
    p = argparse.ArgumentParser(prog="wisnee", description="Orquestador de despliegue de Wisnee")
    sub = p.add_subparsers(dest="command", required=True)

    pi = sub.add_parser("init", help="Configura y levanta el stack por primera vez")
    pi.add_argument("--force", action="store_true", help="Reconfigurar (REGENERA secrets)")
    pi.add_argument("--skip-provision", action="store_true", help="No correr Ansible (sistema ya provisto)")
    pi.add_argument("--no-harden", action="store_true", help="No endurecer SSH a key-only")
    pi.set_defaults(func=cmd_init)

    pu = sub.add_parser("update", help="Baja imágenes nuevas, migra y recrea")
    pu.set_defaults(func=cmd_update)

    ps = sub.add_parser("seed", help="(Demo) resetea la BD y siembra datos demo")
    ps.add_argument("--yes", action="store_true", help="Sin confirmación")
    ps.set_defaults(func=cmd_seed)

    pl = sub.add_parser("logs", help="Sigue los logs")
    pl.add_argument("service", nargs="*", help="Servicio(s) (vacío = todos)")
    pl.set_defaults(func=cmd_logs)

    sub.add_parser("status", help="Estado de los servicios").set_defaults(func=cmd_status)
    sub.add_parser("cert", help="Emitir/renovar el certificado TLS").set_defaults(func=cmd_cert)
    sub.add_parser("backup", help="pg_dump de la base").set_defaults(func=cmd_backup)
    sub.add_parser("credentials", help="Muestra el credentials.txt").set_defaults(func=cmd_credentials)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as e:
        _die(f"Falló un comando (exit {e.returncode}).")
    except KeyboardInterrupt:
        _die("Cancelado.", 130)
