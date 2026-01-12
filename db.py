import os
import psycopg2
import traceback
import time
from datetime import datetime, timezone
from psycopg2 import pool

# Pool de conexões
connection_pool = None

def init_db_pool():
    """Inicializa pool de conexões"""
    global connection_pool
    try:
        connection_pool = psycopg2.pool.SimpleConnectionPool(
            1, 10,
            host=os.getenv('DB_HOST', 'grafana-postgres'),
            dbname=os.getenv('DB_NAME', 'grafana'),
            user=os.getenv('DB_USER', 'grafana_auditor'),
            password=os.getenv('DB_PASSWORD', 'CHANGE_ME'),
            connect_timeout=5
        )
        print("✅ Pool de conexões PostgreSQL inicializado")
        test_and_init_db()
    except Exception as e:
        print(f"🔥 Erro ao criar pool: {e}")
        connection_pool = None

def get_conn():
    """Obtém conexão do pool com retry"""
    global connection_pool
    if connection_pool is None:
        init_db_pool()

    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = connection_pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return conn
        except Exception as e:
            print(f"⚠️ Tentativa {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            if attempt == 1:
                init_db_pool()
            else:
                raise e

def return_conn(conn):
    """Retorna conexão ao pool"""
    try:
        connection_pool.putconn(conn)
    except:
        pass

def test_and_init_db():
    """Testa conexão e cria tabelas se não existirem"""
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'dashboard_access_logs'
                );
            """)
            exists = cur.fetchone()[0]

        if not exists:
            print("📦 Criando tabelas no banco de dados...")
            create_tables()
            print("✅ Tabelas criadas com sucesso")
        else:
            print("✅ Tabelas já existem no banco")

    except Exception as e:
        print(f"🔥 Erro ao verificar/criar tabelas: {e}")
        raise
    finally:
        if conn:
            return_conn(conn)

def create_tables():
    """Cria todas as tabelas necessárias"""
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

                CREATE INDEX IF NOT EXISTS idx_dashboard_access_logs_uid
                    ON dashboard_access_logs(dashboard_uid);

                CREATE INDEX IF NOT EXISTS idx_dashboard_access_logs_time
                    ON dashboard_access_logs(accessed_at);
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

                CREATE INDEX IF NOT EXISTS idx_dashboard_usage_metrics_name
                    ON dashboard_usage_metrics(dashboard_name);

                CREATE INDEX IF NOT EXISTS idx_dashboard_usage_metrics_access
                    ON dashboard_usage_metrics(last_access);
            """)

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise
    finally:
        return_conn(conn)

def save_access(username, dashboard_uid, ts):
    """Salva acesso na tabela de logs com retry"""
    max_retries = 2
    for attempt in range(max_retries):
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO dashboard_access_logs (username, dashboard_uid, accessed_at) VALUES (%s, %s, %s)",
                    (username, dashboard_uid, ts)
                )
            conn.commit()
            return_conn(conn)
            print(f"✅ Access saved: {username} -> {dashboard_uid}")
            return
        except Exception as e:
            print(f"🔥 Tentativa {attempt + 1}/{max_retries} erro ao salvar acesso: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                traceback.print_exc()

def inc_metric(dashboard_uid, dashboard_name, update_name_only=False):
    """Incrementa visualizações ou corrige nome do dashboard"""
    if dashboard_name is None:
        dashboard_name = dashboard_uid
    
    dashboard_name = str(dashboard_name).strip()
    if not dashboard_name or dashboard_name == 'N/A':
        dashboard_name = dashboard_uid

    max_retries = 2
    for attempt in range(max_retries):
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                if update_name_only:
                    if dashboard_name != dashboard_uid:
                        cur.execute("""
                            UPDATE dashboard_usage_metrics
                            SET dashboard_name = %s, updated_at = NOW()
                            WHERE dashboard_uid = %s
                        """, (dashboard_name, dashboard_uid))

                else:
                    cur.execute("""
                        INSERT INTO dashboard_usage_metrics (dashboard_uid, views, last_access, dashboard_name)
                        VALUES (%s, 1, NOW(), %s)
                        ON CONFLICT (dashboard_uid)
                        DO UPDATE SET
                            views = dashboard_usage_metrics.views + 1,
                            last_access = NOW(),
                            updated_at = NOW()
                        WHERE dashboard_usage_metrics.dashboard_uid = %s;
                    """, (dashboard_uid, dashboard_name, dashboard_uid))

            conn.commit()
            return_conn(conn)
            return

        except Exception as e:
            print(f"🔥 Tentativa {attempt + 1}/{max_retries} erro em inc_metric: {e}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                traceback.print_exc()

def get_dashboards_last_access_simple(limit=50):
    """Retorna dashboards acessados recentemente em JSON"""
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(NULLIF(dashboard_name, ''), dashboard_uid) as name,
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
            if last_access:
                iso_date = last_access.isoformat() + "Z"
                human_date = last_access.strftime("%Y-%m-%d %H:%M:%S")
            else:
                iso_date = None
                human_date = None

            result.append({
                "Dashboard": name,
                "Last Access": iso_date,
                "Last Access Human": human_date,
                "Views": int(views),
                "UID": uid,
                "Time": iso_date
            })

        return result

    except Exception as e:
        print(f"🔥 Erro ao buscar dashboards: {e}")
        traceback.print_exc()
        return []

    finally:
        if 'conn' in locals():
            return_conn(conn)