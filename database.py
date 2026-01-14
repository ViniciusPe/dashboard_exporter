import os
import psycopg2
import traceback
import time
from psycopg2 import pool

# Pool de conexões
connection_pool = None


# =========================
# INIT / POOL
# =========================

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
            print(f"⚠️ Tentativa {attempt + 1}/3: {e}")
            time.sleep(2 ** attempt)

    raise Exception("❌ Não foi possível obter conexão com o banco")


def return_conn(conn):
    try:
        connection_pool.putconn(conn)
    except:
        pass


# =========================
# INIT TABLES
# =========================

def test_and_init_db():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'dashboard_access_logs'
                );
            """)
            exists = cur.fetchone()[0]

        if not exists:
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dashboard_access_logs (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(255),
                    dashboard_uid VARCHAR(255),
                    accessed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)

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

        conn.commit()

    except:
        conn.rollback()
        raise
    finally:
        return_conn(conn)


# =========================
# WRITE
# =========================

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
    if not dashboard_name or dashboard_name == 'N/A':
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
                    updated_at = NOW();
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
                    dashboard_uid,
                    dashboard_name,
                    username,
                    access_count,
                    last_access
                )
                VALUES (%s, %s, %s, 1, %s)
                ON CONFLICT (dashboard_uid, username)
                DO UPDATE SET
                    access_count = dashboard_user_usage.access_count + 1,
                    last_access = EXCLUDED.last_access,
                    dashboard_name = EXCLUDED.dashboard_name,
                    updated_at = NOW();
            """, (dashboard_uid, dashboard_name, username, ts))
        conn.commit()
    finally:
        return_conn(conn)


# =========================
# READ — DASHBOARDS (GERAL)
# =========================

def get_dashboards_last_access_simple(from_ts=None, to_ts=None, limit=50):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    l.dashboard_uid,
                    COALESCE(m.dashboard_name, l.dashboard_uid) AS dashboard_name,
                    COUNT(*) AS views,
                    MAX(l.accessed_at) AS last_access
                FROM dashboard_access_logs l
                LEFT JOIN dashboard_usage_metrics m
                    ON m.dashboard_uid = l.dashboard_uid
                WHERE
                    (%s IS NULL OR l.accessed_at >= to_timestamp(%s / 1000))
                AND (%s IS NULL OR l.accessed_at <= to_timestamp(%s / 1000))
                GROUP BY l.dashboard_uid, dashboard_name
                ORDER BY last_access DESC
                LIMIT %s;
            """, (from_ts, from_ts, to_ts, to_ts, limit))

            rows = cur.fetchall()

        result = []
        for uid, name, views, last_access in rows:
            iso = last_access.isoformat() + "Z" if last_access else None

            result.append({
                "Dashboard": name,
                "Last Access": iso,
                "Last Access Human": last_access.strftime("%Y-%m-%d %H:%M:%S") if last_access else None,
                "Views": int(views),
                "UID": uid,
                "Time": iso
            })

        return result

    except Exception as e:
        print(f"🔥 Erro last-access: {e}")
        traceback.print_exc()
        return []

    finally:
        if 'conn' in locals():
            return_conn(conn)


# =========================
# READ — DASHBOARD x USER (TIMEPICKER)
# =========================

def get_dashboards_users_view(from_ts=None, to_ts=None, limit=500):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    l.dashboard_uid,
                    COALESCE(m.dashboard_name, l.dashboard_uid) AS dashboard_name,
                    l.username,
                    COUNT(*) AS views,
                    MAX(l.accessed_at) AS last_access
                FROM dashboard_access_logs l
                LEFT JOIN dashboard_usage_metrics m
                    ON m.dashboard_uid = l.dashboard_uid
                WHERE
                    (%s IS NULL OR l.accessed_at >= to_timestamp(%s / 1000))
                AND (%s IS NULL OR l.accessed_at <= to_timestamp(%s / 1000))
                GROUP BY l.dashboard_uid, dashboard_name, l.username
                ORDER BY last_access DESC
                LIMIT %s;
            """, (from_ts, from_ts, to_ts, to_ts, limit))

            rows = cur.fetchall()

        result = []
        for uid, name, user, views, last_access in rows:
            iso = last_access.isoformat() + "Z" if last_access else None

            result.append({
                "UID": uid,
                "Dashboard": name,
                "User": user,
                "Views": int(views),
                "Last Access": iso,
                "Time": iso
            })

        return result

    except Exception as e:
        print(f"🔥 Erro users_dashs_view: {e}")
        traceback.print_exc()
        return []

    finally:
        if 'conn' in locals():
            return_conn(conn)
