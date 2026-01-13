import os
import psycopg2
import traceback
import time
from datetime import datetime
from psycopg2 import pool

# ============================================================
# POOL DE CONEXÕES
# ============================================================
connection_pool = None


def init_db_pool():
    global connection_pool
    try:
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv('DB_HOST', 'tracker-postgres'),
            dbname=os.getenv('DB_NAME', 'tracker'),
            user=os.getenv('DB_USER', 'tracker_user'),
            password=os.getenv('DB_PASSWORD', 'CHANGE_ME'),
            connect_timeout=5
        )
        print("✅ Pool de conexões PostgreSQL inicializado")
        test_and_init_db()
    except Exception as e:
        print(f"🔥 Erro ao criar pool: {e}")
        connection_pool = None


def get_conn():
    global connection_pool
    if connection_pool is None:
        init_db_pool()

    for attempt in range(3):
        try:
            conn = connection_pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception as e:
            print(f"⚠️ Tentativa {attempt + 1}/3 erro DB: {e}")
            time.sleep(2 ** attempt)
    raise Exception("❌ Não foi possível obter conexão com o banco")


def return_conn(conn):
    try:
        connection_pool.putconn(conn)
    except:
        pass


# ============================================================
# INIT DB
# ============================================================
def test_and_init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
            """)
            tables = [r[0] for r in cur.fetchall()]

        if 'dashboard_access_logs' not in tables:
            print("📦 Criando tabelas no banco...")
            create_tables()
        else:
            print("✅ Tabelas já existem no banco")

    finally:
        return_conn(conn)


def create_tables():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # LOG BRUTO
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_access_logs (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255),
                    dashboard_uid VARCHAR(255),
                    accessed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # MÉTRICA GERAL
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_usage_metrics (
                    dashboard_uid VARCHAR(255) PRIMARY KEY,
                    views INTEGER DEFAULT 0,
                    last_access TIMESTAMP,
                    dashboard_name VARCHAR(255),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """)

            # MÉTRICA POR USUÁRIO
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_user_usage (
                    id SERIAL PRIMARY KEY,
                    dashboard_uid VARCHAR(255) NOT NULL,
                    dashboard_name VARCHAR(255) NOT NULL,
                    username VARCHAR(255) NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_access TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE (dashboard_uid, username)
                );
            """)

        conn.commit()
        print("✅ Todas as tabelas criadas com sucesso")

    except Exception as e:
        conn.rollback()
        raise
    finally:
        return_conn(conn)


# ============================================================
# INSERTS / UPDATES
# ============================================================
def save_access(username, dashboard_uid, ts):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO dashboard_access_logs (username, dashboard_uid, accessed_at)
                VALUES (%s, %s, %s)
            """, (username, dashboard_uid, ts))
        conn.commit()
    finally:
        return_conn(conn)


def inc_metric(dashboard_uid, dashboard_name):
    if not dashboard_name:
        dashboard_name = dashboard_uid

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO dashboard_usage_metrics (dashboard_uid, views, last_access, dashboard_name)
                VALUES (%s, 1, NOW(), %s)
                ON CONFLICT (dashboard_uid)
                DO UPDATE SET
                    views = dashboard_usage_metrics.views + 1,
                    last_access = NOW(),
                    dashboard_name = EXCLUDED.dashboard_name,
                    updated_at = NOW()
            """, (dashboard_uid, dashboard_name))
        conn.commit()
    finally:
        return_conn(conn)


def inc_user_dashboard_metric(dashboard_uid, dashboard_name, username, ts):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO dashboard_user_usage (
                    dashboard_uid, dashboard_name, username, access_count, last_access
                )
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (dashboard_uid, username)
                DO UPDATE SET
                    access_count = dashboard_user_usage.access_count + 1,
                    last_access = EXCLUDED.last_access,
                    dashboard_name = EXCLUDED.dashboard_name,
                    updated_at = NOW()
            """, (dashboard_uid, dashboard_name, username, ts))
        conn.commit()
    finally:
        return_conn(conn)


# ============================================================
# QUERIES PARA API
# ============================================================
def get_dashboards_last_access_simple(limit=50):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(dashboard_name,''), dashboard_uid),
                    last_access,
                    views,
                    dashboard_uid
                FROM dashboard_usage_metrics
                ORDER BY last_access DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()

        result = []
        for name, last_access, views, uid in rows:
            iso = last_access.isoformat() + "Z"
            result.append({
                "Dashboard": name,
                "UID": uid,
                "Views": views,
                "Last Access": iso,
                "Time": iso
            })
        return result

    finally:
        return_conn(conn)


def get_dashboards_users_view():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    dashboard_uid,
                    dashboard_name,
                    username,
                    access_count,
                    last_access
                FROM dashboard_user_usage
                ORDER BY last_access DESC
            """)
            rows = cur.fetchall()

        result = []
        for uid, name, user, count, last_access in rows:
            iso = last_access.isoformat() + "Z"
            result.append({
                "UID": uid,
                "Dashboard": name,
                "User": user,
                "Views": count,
                "Last Access": iso,
                "Time": iso
            })

        return result

    finally:
        return_conn(conn)
