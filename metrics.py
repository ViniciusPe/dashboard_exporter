from flask import Response
from prometheus_client import Counter, generate_latest, REGISTRY
import re
from datetime import datetime
import psycopg2
import os

# Remove métricas default do REGISTRY
for collector in list(REGISTRY._collector_to_names.keys()):
    REGISTRY.unregister(collector)

dashboard_views = Counter(
    "grafana_dashboard_views_total",
    "Total de acessos por dashboard",
    ["dashboard_uid", "dashboard_name"]
)

def get_db_connection():
    """Cria uma nova conexão direta com o banco"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'grafana-postgres'),
        dbname=os.getenv('DB_NAME', 'grafana'),
        user=os.getenv('DB_USER', 'grafana_auditor'),
        password=os.getenv('DB_PASSWORD', 'CHANGE_ME'),
        connect_timeout=5
    )

def sanitize_prometheus_label(value):
    if not value:
        return "unknown"
    value = re.sub(r'http[s]?://\S+', '', str(value))
    value = re.sub(r'[^a-zA-Z0-9_:]', '_', value)
    value = re.sub(r'_+', '_', value)
    return value.strip('_')[:100]

def metrics_handler():
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT dashboard_uid, views, dashboard_name FROM dashboard_usage_metrics ORDER BY dashboard_uid;")
            rows = cur.fetchall()
        
        # Limpa métricas anteriores
        dashboard_views.clear()

        for uid, views, name in rows:
            if views <= 0:
                continue
                
            clean_name = sanitize_prometheus_label(name if name and name != uid else f"dashboard_{uid[:8]}")
            clean_uid = sanitize_prometheus_label(uid)
            
            # Incrementa corretamente
            dashboard_views.labels(
                dashboard_uid=clean_uid,
                dashboard_name=clean_name
            ).inc(int(views))

        return Response(generate_latest(), mimetype="text/plain")

    except Exception as e:
        print("🔥 Erro ao gerar métricas:", e)
        import traceback
        traceback.print_exc()
        return Response("# erro\n", mimetype="text/plain")
    finally:
        if conn:
            conn.close()