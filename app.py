from flask import Flask, jsonify
from metrics import metrics_handler
from log_reader import start_log_monitor
from database import (
    init_db_pool,
    get_dashboards_last_access_simple,
    get_dashboards_users_view
)
import threading
import time
from datetime import datetime

app = Flask(__name__)

# =========================
# INIT DB
# =========================
print("🔧 Inicializando pool de conexões PostgreSQL...")
init_db_pool()

# =========================
# CORS
# =========================
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return response

# =========================
# ENDPOINTS
# =========================
@app.route("/metrics")
def metrics():
    return metrics_handler()

@app.route("/api/dashboards/last-access")
def dashboards_last_access():
    try:
        return jsonify(get_dashboards_last_access_simple(limit=50))
    except Exception as e:
        print(f"🔥 Erro last-access: {e}")
        return jsonify([])

@app.route("/api/dashboards/users_dashs_view")
def dashboards_users_view():
    try:
        return jsonify(get_dashboards_users_view(limit=200))
    except Exception as e:
        print(f"🔥 Erro users_dashs_view: {e}")
        return jsonify([])

@app.route("/health")
def health():
    from database import get_conn, return_conn
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return_conn(conn)

        return jsonify({
            "status": "healthy",
            "service": "grafana-dashboard-tracker",
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500

@app.route("/debug/db")
def debug_db():
    from database import get_conn, return_conn
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 'access_logs', COUNT(*) FROM dashboard_access_logs
                UNION ALL
                SELECT 'usage_metrics', COUNT(*) FROM dashboard_usage_metrics
                UNION ALL
                SELECT 'user_usage', COUNT(*) FROM dashboard_user_usage
            """)
            rows = cur.fetchall()
        return_conn(conn)

        return jsonify({
            "tables": [{"table": r[0], "count": r[1]} for r in rows]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =========================
# LOG MONITOR THREAD
# =========================
def start_background_monitor():
    try:
        print("🚀 Iniciando monitor de logs em 3 segundos...")
        time.sleep(3)
        start_log_monitor()
    except Exception as e:
        print(f"🔥 ERRO CRÍTICO no monitor: {e}")
        import traceback
        traceback.print_exc()

threading.Thread(
    target=start_background_monitor,
    daemon=True
).start()

print("✅ Thread do monitor iniciada")

if __name__ == "__main__":
    print("📡 Endpoints ativos:")
    print(" - /metrics")
    print(" - /api/dashboards/last-access")
    print(" - /api/dashboards/users_dashs_view")
    print(" - /health")
    print(" - /debug/db")

    app.run(host="0.0.0.0", port=9109)
