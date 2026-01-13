from flask import Flask, jsonify
from metrics import metrics_handler
from log_reader import start_log_monitor
from db import (
    get_dashboards_last_access_simple,
    get_dashboards_users_view,
    init_db_pool
)
import threading
import time
from datetime import datetime

app = Flask(__name__)

# Inicializa pool de conexões
print("🔧 Inicializando pool de conexões PostgreSQL...")
init_db_pool()

# Middleware CORS
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    return response

@app.route("/metrics")
def metrics():
    return metrics_handler()

@app.route("/api/dashboards/last-access")
def dashboards_last_access():
    """Endpoint para Infinity Plugin"""
    try:
        dashboards = get_dashboards_last_access_simple(limit=50)
        return jsonify(dashboards)
    except Exception as e:
        print(f"🔥 Erro: {e}")
        return jsonify([])

# ✅ NOVO ENDPOINT — DASHBOARD x USUÁRIO
@app.route("/api/dashboards/users_dashs_view")
def dashboards_users_dashs_view():
    """Endpoint por dashboard x usuário"""
    try:
        data = get_dashboards_users_view(limit=200)
        return jsonify(data)
    except Exception as e:
        print(f"🔥 Erro users_dashs_view: {e}")
        return jsonify([])

@app.route("/health")
def health():
    """Health check que verifica conexão com DB"""
    from db import get_conn, return_conn

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 as status")
            result = cur.fetchone()
        return_conn(conn)

        db_status = "healthy" if result and result[0] == 1 else "unhealthy"

        return jsonify({
            "status": "healthy",
            "database": db_status,
            "service": "grafana-dashboard-tracker",
            "timestamp": datetime.now().isoformat(),
            "mode": "kubernetes-sidecar-persistent"
        })

    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "database": "connection_failed",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route("/debug/db")
def debug_db():
    """Endpoint de debug para verificar banco"""
    from db import get_conn, return_conn

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 'access_logs' as table, COUNT(*) as count FROM dashboard_access_logs
                UNION ALL
                SELECT 'usage_metrics' as table, COUNT(*) as count FROM dashboard_usage_metrics
                UNION ALL
                SELECT 'user_usage' as table, COUNT(*) as count FROM dashboard_user_usage
            """)
            results = cur.fetchall()
        return_conn(conn)

        return jsonify({
            "tables": [{"table": r[0], "count": r[1]} for r in results],
            "timestamp": datetime.now().isoformat()
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def start_background_monitor():
    """Inicia o monitor de logs em background"""
    try:
        print("🚀 Iniciando monitor de logs do Grafana em 3 segundos...")
        time.sleep(3)
        start_log_monitor()
    except Exception as e:
        print(f"🔥 ERRO CRÍTICO no monitor: {e}")
        import traceback
        traceback.print_exc()

# Inicia monitor em background
monitor_thread = threading.Thread(target=start_background_monitor, daemon=True)
monitor_thread.start()
print("✅ Thread do monitor iniciada em background")

if __name__ == "__main__":
    print("✅ Backend sidecar iniciado com persistência!")
    print("📡 Endpoints disponíveis:")
    print("   - /metrics")
    print("   - /api/dashboards/last-access")
    print("   - /api/dashboards/users_dashs_view")
    print("   - /health")
    print("   - /debug/db")

    app.run(host="0.0.0.0", port=9109)
