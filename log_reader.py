import re
import time
from datetime import datetime
from db import save_access, inc_metric
import subprocess

# =========================
# REGEX PARA LOGS DO GRAFANA (ARO)
# =========================

# Usuário autenticado
PATTERN_USER = re.compile(r'uname=([^\s]+)')

# UID do dashboard via API (principal no ARO)
PATTERN_API_UID = re.compile(r'path=/api/dashboards/uid/([a-zA-Z0-9\-_]+)')

# UID e slug via UI no referer (fallback para nome)
PATTERN_REFERER = re.compile(
    r'referer="[^"]*/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^"?/]+)'
)

# =========================
# CONTROLE DE DUPLICAÇÃO
# =========================
last_access_time = {}
DEDUPE_SECONDS = 5


def extract_username(log_line):
    match = PATTERN_USER.search(log_line)
    return match.group(1) if match else None


def extract_dashboard_uid(log_line):
    # 1️⃣ UID pela API (caso principal)
    match = PATTERN_API_UID.search(log_line)
    if match:
        return match.group(1)

    # 2️⃣ Fallback via referer
    match = PATTERN_REFERER.search(log_line)
    if match:
        return match.group("uid")

    return None


def extract_dashboard_name(log_line, dashboard_uid):
    # Nome/slug só existe no referer
    match = PATTERN_REFERER.search(log_line)
    if match:
        return match.group("slug")

    # Fallback final: UID
    return dashboard_uid


def should_process_access(username, dashboard_uid):
    key = f"{username}_{dashboard_uid}"
    now = time.time()

    if key in last_access_time:
        if now - last_access_time[key] < DEDUPE_SECONDS:
            return False

    last_access_time[key] = now
    return True


def process_log_line(log_line):
    """
    Processa apenas acessos reais a dashboards
    """
    try:
        # Só nos interessa acesso ao dashboard pela API
        if "/api/dashboards/uid/" not in log_line:
            return

        username = extract_username(log_line)
        dashboard_uid = extract_dashboard_uid(log_line)

        if not username or not dashboard_uid:
            return

        if not should_process_access(username, dashboard_uid):
            return

        dashboard_name = extract_dashboard_name(log_line, dashboard_uid)
        accessed_at = datetime.utcnow()

        print(
            f"🔥 Dashboard acessado → "
            f"user={username} uid={dashboard_uid} dash={dashboard_name} at={accessed_at}"
        )

        # Persistência
        save_access(username, dashboard_uid, accessed_at)
        inc_metric(dashboard_uid, dashboard_name)

    except Exception as e:
        print(f"🔥 Erro ao processar log: {e}")


def read_logs():
    """
    Lê logs do container Grafana no ARO/OpenShift
    """
    print("📡 Monitorando logs do Grafana (ARO/OpenShift)...")

    cmd = [
        "oc", "logs",
        "-n", "nm-observ",
        "-l", "app.kubernetes.io/instance=lgtm-deploy",
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


# =========================
# ENTRY POINT ESPERADO PELO app.py
# =========================
def start_log_monitor():
    """
    Entry-point compatível com app.py
    """
    read_logs()


# Execução direta (debug local)
if __name__ == "__main__":
    start_log_monitor()
