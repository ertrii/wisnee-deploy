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
    print(f"  Setup:  https://{answers['domain']}/?token={secrets['INIT_TOKEN']}")


_TAG_VARS = {"tag": "TAG", "server": "SERVER_TAG", "web": "WEB_TAG",
             "wa": "WA_TAG", "mk": "MK_TAG"}


def cmd_update(args):
    cfg = _require_configured()

    # Overrides de tag por servicio: persistirlos en compose/.env antes de
    # pull/up. Sin overrides, recrea con los tags actuales (comportamiento previo).
    overrides = {_TAG_VARS[k]: getattr(args, k) for k in _TAG_VARS
                 if getattr(args, k, None)}
    # Un `--tag` global expresa "todos los servicios a esta versión": limpia los
    # pines por-servicio (WEB_TAG/SERVER_TAG/...) que hayan quedado de un deploy
    # `--web`/`--server` previo, salvo los que se vuelvan a pasar explícito ahora.
    # Sin esto, el compose `${WEB_TAG:-${TAG:-latest}}` deja el pin viejo ganando
    # sobre `--tag` y un servicio se queda atrás en la versión anterior.
    if args.tag:
        for var in ("SERVER_TAG", "WEB_TAG", "WA_TAG", "MK_TAG"):
            if var not in overrides:
                cfg.pop(var, None)
    if overrides or args.tag:
        cfg.update(overrides)
        config.write_env_file(config.COMPOSE_ENV, cfg)
        print("→ Tags fijados: " + ", ".join(f"{k}={v}" for k, v in cfg.items()
                                              if k in _TAG_VARS.values()))

    env = cfg.get("APP_ENV", "prod")

    # Re-renderizar nginx por si cambió el template (p. ej. sub_filter de
    # og:image). Es idempotente y barato.
    domain = cfg.get("DOMAIN")
    if domain:
        render.render_nginx(domain)

    print("→ Bajando imágenes nuevas…")
    runner.compose(["pull"], env)
    print("→ Aplicando (migrate one-shot corre antes del server)…")
    runner.compose(["up", "-d"], env)

    # Aplicar el nginx re-renderizado (el bind mount ya está; basta un reload).
    if domain:
        try:
            runner.nginx_reload(env)
        except subprocess.CalledProcessError:
            pass  # el proxy se recrea con up -d si hiciera falta

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
    # SEED_FORCE=1: el comando explícito SÍ resetea. El seed automático del
    # `up -d` corre sin esta variable → es seed-once (no borra si ya hay datos),
    # para que un `update` no destruya lo creado a mano en la demo.
    runner.compose(["run", "--rm", "-e", "SEED_FORCE=1", "seed"], env)
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
    pu.add_argument("--tag", help="Fija el TAG global de todos los servicios")
    pu.add_argument("--server", help="Fija solo el tag de wisnet-server")
    pu.add_argument("--web", help="Fija solo el tag del frontend (wisnet)")
    pu.add_argument("--wa", help="Fija solo el tag de wa-bridge")
    pu.add_argument("--mk", help="Fija solo el tag de mk-bridge")
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
