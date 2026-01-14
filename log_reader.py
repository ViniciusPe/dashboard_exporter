import re
import time
import shutil
from datetime import datetime
import subprocess
from database import (
    save_access,
    inc_metric,
    inc_user_dashboard_metric
)

# ============================================================
# REGEX
# ============================================================

# Usuário autenticado
PATTERN_USER = re.compile(r'uname=([^\s]+)')

# Dashboard acessado DIRETAMENTE (quando existir)
PATTERN_PATH_DASH = re.compile(
    r'path=/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s/?]+)'
)

# Dashboard acessado via API
PATTERN_API_UID = re.compile(
    r'path=/api/dashboards/uid/(?P<uid>[a-zA-Z0-9\-_]+)'
)

# Dashboard acessado VIA REFERER (SEU CASO REAL)
PATTERN_REFERER = re.compile(
    r'referer="[^"]*/d/(?P<uid>[a-zA-Z0-9\-_]+)/(?P<slug>[^\s"?/]+)'
)

# Deduplicação
last_access_time = {}
DEDUPE_SECONDS = 5


# ============================================================
# EXTRACTORS
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

    if key in last_access_time and now - last_access_time[key] < DEDUPE_SECONDS:
        return False

    last_access_time[key] = now
    return True


# ============================================================
# PROCESSAMENTO DO LOG
# ============================================================

def process_log_line(line):
    """
    IMPORTANTE:
    No seu Grafana, o acesso ao dashboard aparece NO REFERER,
    não no path principal.
    """

    # 🔴 FILTRO CORRETO (ESSA ERA A FALHA)
    if "referer=" not in line or "/d/" not in line:
        return

    username = extract_username(line)
    uid = extract_dashboard_uid(line)

    if not username or not uid:
        return

    if not should_process(username, uid):
        return

    dash_name = extract_dashboard_name(line, uid)
    ts = datetime.utcnow()

    print(f"🔥 Dashboard acessado → user={username} uid={uid} dash={dash_name}")

    # Escrita no banco
    save_access(username, uid, ts)
    inc_metric(uid, dash_name)
    inc_user_dashboard_metric(uid, dash_name, username, ts)


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
    print("📡 Monitorando logs do Grafana...")

    cli = resolve_cli()
    if not cli:
        print("❌ kubectl/oc não encontrado no container")
        return

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
        if line:
            process_log_line(line.strip())


def start_log_monitor():
    read_logs()


if __name__ == "__main__":
    start_log_monitor()
