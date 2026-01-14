import os
import psycopg2
import time
import traceback
from datetime import datetime
from psycopg2 import pool

# ============================================================
# POOL
# ============================================================
connection_pool = None


def init_db_pool():
    global connection_pool
    try:
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv("DB_HOST", "tracker-postgres"),
            dbname=os.getenv("DB_NAME", "tracker"),
            user=os.getenv("DB_USER", "tracker_user"),
            password=os.getenv("DB_PASSWORD", "CHANGE_ME"),
            connect_timeout=5
        )
        print("✅ Pool de conexões PostgreSQL inicializado")
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


# ============================================================
# WRITE — EXISTENTE (NÃO MEXER)
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
    if not dashboard_name or dashboard_name == "N/A":
        dashboard_name = dashboard_uid

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO dashboard_usage_metrics (
                    dashboard_uid, views, last_access, dashboard_name
                )
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


# ============================================================
# READ — DASHBOARDS (LAST-ACCESS) ✅ CORRIGIDO
# ============================================================
def get_dashboards_last_access_simple(limit=50, from_ts=None, to_ts=None):
    conn = get_conn()
    try:
        # ====================================================
        # COM TIME RANGE → recalcula views via logs
        # ====================================================
        if from_ts and to_ts:
            from_dt = datetime.utcfromtimestamp(int(from_ts) / 1000)
            to_dt = datetime.utcfromtimestamp(int(to_ts) / 1000)

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
                    WHERE l.accessed_at BETWEEN %s AND %s
                    GROUP BY l.dashboard_uid, m.dashboard_name
                    ORDER BY last_access DESC
                    LIMIT %s;
                """, (from_dt, to_dt, limit))

                rows = cur.fetchall()

            result = []
            for uid, name, views, last_access in rows:
                iso = last_access.isoformat() + "Z"
                result.append({
                    "Dashboard": name,
                    "UID": uid,
                    "Views": int(views),
                    "Last Access": iso,
                    "Time": iso
                })

            return result

        # ====================================================
        # SEM TIME RANGE → comportamento antigo (histórico)
        # ====================================================
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COALESCE(NULLIF(dashboard_name, ''), dashboard_uid),
                    last_access,
                    views,
                    dashboard_uid
                FROM dashboard_usage_metrics
                WHERE last_access IS NOT NULL
                ORDER BY last_access DESC
                LIMIT %s;
            """, (limit,))

            rows = cur.fetchall()

        result = []
        for name, last_access, views, uid in rows:
            iso = last_access.isoformat() + "Z"
            result.append({
                "Dashboard": name,
                "UID": uid,
                "Views": int(views),
                "Last Access": iso,
                "Time": iso
            })

        return result

    except Exception as e:
        print(f"🔥 Erro get_dashboards_last_access_simple: {e}")
        traceback.print_exc()
        return []

    finally:
        return_conn(conn)


# ============================================================
# READ — DASHBOARD x USUÁRIO (INALTERADO)
# ============================================================
def get_dashboards_users_view(limit=200, from_ts=None, to_ts=None):
    conn = get_conn()
    try:
        query = """
            SELECT
                dashboard_uid,
                dashboard_name,
                username,
                access_count,
                last_access
            FROM dashboard_user_usage
            WHERE last_access IS NOT NULL
        """
        params = []

        if from_ts and to_ts:
            from_dt = datetime.utcfromtimestamp(int(from_ts) / 1000)
            to_dt = datetime.utcfromtimestamp(int(to_ts) / 1000)
            query += " AND last_access BETWEEN %s AND %s"
            params.extend([from_dt, to_dt])

        query += " ORDER BY last_access DESC LIMIT %s"
        params.append(limit)

        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        result = []
        for uid, name, user, views, last_access in rows:
            iso = last_access.isoformat() + "Z"
            result.append({
                "UID": uid,
                "Dashboard": name,
                "User": user,
                "Views": int(views),
                "Last Access": iso,
                "Time": iso
            })

        return result

    finally:
        return_conn(conn)
