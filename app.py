from flask import Flask, jsonify, request
from metrics import metrics_handler
from log_reader import start_log_monitor
from database import (
    get_dashboards_last_access_simple,
    get_dashboards_users_view,
    init_db_pool
)
import threading
import time
from datetime import datetime

app = Flask(__name__)

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
    from_ts = request.args.get("from", type=int)
    to_ts = request.args.get("to", type=int)

    return jsonify(
        get_dashboards_last_access_simple(
            from_ts=from_ts,
            to_ts=to_ts,
            limit=50
        )
    )


@app.route("/api/dashboards/users_dashs_view")
def dashboards_users_dashs_view():
    from_ts = request.args.get("from", type=int)
    to_ts = request.args.get("to", type=int)

    return jsonify(
        get_dashboards_users_view(
            from_ts=from_ts,
            to_ts=to_ts,
            limit=500
        )
    )


@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "service": "grafana-dashboard-tracker",
        "timestamp": datetime.utcnow().isoformat()
    })


def start_background_monitor():
    time.sleep(3)
    start_log_monitor()


monitor_thread = threading.Thread(
    target=start_background_monitor,
    daemon=True
)
monitor_thread.start()

print("✅ Backend iniciado")
print("📡 Endpoints:")
print(" - /api/dashboards/last-access")
print(" - /api/dashboards/users_dashs_view")
print(" - /metrics")
print(" - /health")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9109)
