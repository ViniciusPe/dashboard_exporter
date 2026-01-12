import re
import time
import shutil
from datetime import datetime
from db import save_access, inc_metric
import subprocess

# =========================
# REGEX PARA LOGS DO GRAFANA (ARO)
# =========================

PATTERN_USER = re.compile(r'uname=([^\s]+)')
PATTERN_API_UID = re.compile(r'path=/api/dashboards/uid/([a-zA-Z0-9\-_]+)')
PATTERN_REFERER = re.compile(
    r'referer="[^"]*/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^"?/]+)'
)

# =========================
# CONTROLE DE DUPLICAÇÃO
# =========================
last_access_time = {}
DEDUPE_SECONDS = 5


def extract_username(log_line):
    m = PATTERN_USER.search(log_line)
    return m.group(1) if m else None


def extract_dashboard_uid(log_line):
    m = PATTERN_API_UID.search(log_line)
    if m:
        return m.group(1)

    m2 = PATTERN_REFERER.search(log_line)
    if m2:
        return m2.group("uid")

    return None


def extract_dashboard_name(log_line, dashboard_uid):
    m = PATTERN_REFERER.search(log_line)
    if m:
        return m.group("slug")

    return dashboard_uid


def should_process_access(username, dashboard_uid):
    key = f"{username}_{dashboard_uid}"
    now = time.time()

    if key in last_access_time and now - last_access_time[key] < DEDUPE_SECONDS:
        return False

    last_access_time[key] = now
    return True


def process_log_line(log_line):
    try:
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

        save_access(username, dashboard_uid, accessed_at)
        inc_metric(dashboard_uid, dashboard_name)

    except Exception as e:
        print(f"🔥 Erro ao processar log: {e}")


def resolve_k8s_cli():
    """
    Resolve qual CLI está disponível no container.
    Prioridade: oc > kubectl
    """
    if shutil.which("oc"):
        return "oc"
    if shutil.which("kubectl"):
        return "kubectl"
    return None


def read_logs():
    print("📡 Iniciando monitor de logs do Grafana...")

    cli = resolve_k8s_cli()
    if not cli:
        print("❌ ERRO CRÍTICO: nem 'oc' nem 'kubectl' estão disponíveis no container.")
        print("❌ Não é possível ler logs do Grafana.")
        return

    print(f"✅ Usando CLI: {cli}")

    cmd = [
        cli, "logs",
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
    read_logs()


if __name__ == "__main__":
    start_log_monitor()
