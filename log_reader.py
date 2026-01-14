import re
import time
import shutil
import subprocess
from datetime import datetime

from db import (
    save_access,
    inc_metric,
    inc_user_dashboard_metric
)

# ============================================================
# REGEX
# ============================================================

PATTERN_USER = re.compile(r'uname=([^\s]+)')

# /d/<uid>/<slug>
PATTERN_PATH_DASH = re.compile(
    r'path=/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s/?]+)'
)

# /api/dashboards/uid/<uid>
PATTERN_API_UID = re.compile(
    r'path=/api/dashboards/uid/(?P<uid>[a-zA-Z0-9\-_]+)'
)

# referer="/d/<uid>/<slug>"
PATTERN_REFERER = re.compile(
    r'referer="[^"]*/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s"?/]+)'
)

# ============================================================
# DEDUPE
# ============================================================

last_access_time = {}
DEDUPE_SECONDS = 5


# ============================================================
# EXTRAÇÃO
# ============================================================

def extract_username(line):
    m = PATTERN_USER.search(line)
    return m.group(1) if m else None


def extract_dashboard_uid(line):
    for pattern in (PATTERN_PATH_DASH, PATTERN_API_UID, PATTERN_REFERER):
        m = pattern.search(line)
        if m:
            return m.group("uid")
    return None


def extract_dashboard_name(line, uid):
    for pattern in (PATTERN_PATH_DASH, PATTERN_REFERER):
        m = pattern.search(line)
        if m:
            return m.group("slug")
    return uid


def should_process(username, uid):
    key = f"{username}_{uid}"
    now = time.time()

    if key in last_access_time:
        if now - last_access_time[key] < DEDUPE_SECONDS:
            return False

    last_access_time[key] = now
    return True


# ============================================================
# PROCESSAMENTO
# ============================================================

def process_log_line(line):
    # Só interessa dashboard
    if "/d/" not in line and "/api/dashboards/uid/" not in line:
        return

    username = extract_username(line)
    uid = extract_dashboard_uid(line)

    if not username or not uid:
        return

    if not should_process(username, uid):
        return

    dashboard_name = extract_dashboard_name(line, uid)
    ts = datetime.utcnow()

    print(f"🔥 Dashboard acessado → user={username} uid={uid} dash={dashboard_name}")

    save_access(username, uid, ts)
    inc_metric(uid, dashboard_name)
    inc_user_dashboard_metric(uid, dashboard_name, username, ts)


# ============================================================
# LOG STREAM
# ============================================================

def resolve_cli():
    if shutil.which("kubectl"):
        return "kubectl"
    if shutil.which("oc"):
        return "oc"
    return None


def read_logs():
    cli = resolve_cli()
    if not cli:
        print("❌ kubectl/oc não encontrado no container")
        time.sleep(5)
        return

    print("📡 Conectando aos logs do Grafana...")

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

    # Se sair do loop, o stream morreu
    print("⚠️ Stream de logs finalizado")


# ============================================================
# MONITOR COM RECONEXÃO
# ============================================================

def start_log_monitor():
    print("🚀 Iniciando monitor de logs (com auto-reconnect)")

    while True:
        try:
            read_logs()
        except Exception as e:
            print(f"🔥 ERRO no log reader, reiniciando stream: {e}")

        # Evita loop agressivo
        time.sleep(2)


# ============================================================
# EXECUÇÃO DIRETA
# ============================================================

if __name__ == "__main__":
    start_log_monitor()
