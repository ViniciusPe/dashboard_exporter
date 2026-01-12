import re
import time
import shutil
from datetime import datetime
from db import save_access, inc_metric
import subprocess

# ============================================================
# REGEX BASEADAS NOS LOGS REAIS DO GRAFANA (ARO)
# ============================================================

# Usuário
PATTERN_USER = re.compile(r'uname=([^\s]+)')

# Dashboard via path=/d/<uid>/<slug>
PATTERN_PATH_DASH = re.compile(
    r'path=/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s/?]+)'
)

# Dashboard via API
PATTERN_API_UID = re.compile(
    r'path=/api/dashboards/uid/(?P<uid>[a-zA-Z0-9\-_]+)'
)

# Dashboard via referer
PATTERN_REFERER = re.compile(
    r'referer="[^"]*/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s"?/]+)'
)

# ============================================================
# CONTROLE DE DUPLICAÇÃO
# ============================================================
last_access_time = {}
DEDUPE_SECONDS = 5


def extract_username(line):
    m = PATTERN_USER.search(line)
    return m.group(1) if m else None


def extract_dashboard_uid(line):
    # Prioridade 1: path=/d/<uid>/<slug>
    m = PATTERN_PATH_DASH.search(line)
    if m:
        return m.group("uid")

    # Prioridade 2: API
    m2 = PATTERN_API_UID.search(line)
    if m2:
        return m2.group("uid")

    # Prioridade 3: referer
    m3 = PATTERN_REFERER.search(line)
    if m3:
        return m3.group("uid")

    return None


def extract_dashboard_name(line, uid):
    # Prioridade 1: path=/d/<uid>/<slug>
    m = PATTERN_PATH_DASH.search(line)
    if m:
        return m.group("slug")

    # Prioridade 2: referer
    m2 = PATTERN_REFERER.search(line)
    if m2:
        return m2.group("slug")

    # Fallback seguro
    return uid


def should_process(username, uid):
    key = f"{username}_{uid}"
    now = time.time()

    if key in last_access_time and now - last_access_time[key] < DEDUPE_SECONDS:
        return False

    last_access_time[key] = now
    return True


def process_log_line(line):
    try:
        # Só processa acesso real a dashboard
        if "/d/" not in line and "/api/dashboards/uid/" not in line:
            return

        username = extract_username(line)
        uid = extract_dashboard_uid(line)

        if not username or not uid:
            return

        if not should_process(username, uid):
            return

        dash_name = extract_dashboard_name(line, uid)
        ts = datetime.utcnow()

        print(
            f"🔥 Dashboard acessado → "
            f"user={username} uid={uid} dash={dash_name}"
        )

        save_access(username, uid, ts)
        inc_metric(uid, dash_name)

    except Exception as e:
        print(f"🔥 Erro ao processar log: {e}")


def resolve_cli():
    if shutil.which("kubectl"):
        return "kubectl"
    if shutil.which("oc"):
        return "oc"
    return None


def read_logs():
    print("📡 Iniciando captura de logs do Grafana...")

    cli = resolve_cli()
    if not cli:
        print("❌ ERRO: nem kubectl nem oc existem no container")
        return

    print(f"✅ Usando CLI: {cli}")

    # COMANDO IDÊNTICO AO TESTADO MANUALMENTE
    cmd = [
        cli, "logs",
        "-n", "nm-observ",
        "-l", "app.kubernetes.io/name=grafana",
        "-c", "grafana",
        "-f",
        "--tail=0"
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    for line in iter(proc.stdout.readline, ""):
        line = line.strip()
        if line:
            process_log_line(line)


# ============================================================
# ENTRY POINT ESPERADO PELO app.py
# ============================================================
def start_log_monitor():
    read_logs()


if __name__ == "__main__":
    start_log_monitor()
