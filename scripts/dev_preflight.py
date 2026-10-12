"""
Development Environment Preflight Check.
Verifies configuration, PostgreSQL database health, Redis connectivity,
and ensures database schema tables are created before starting servers.
STRICT SAFETY INVARIANT: ZERO OUTBOUND, ZERO EMAILS, ZERO LIVE LLM CALLS.
"""
import os
import sys
import time
import shutil

# Add backend directory to sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

def run_preflight():
    print("=" * 65)
    print("OPPORTUNITY PLATFORM — LOCAL DEV PREFLIGHT CHECK")
    print("=" * 65)

    # 1. Configuration Check
    backend_env = os.path.join(BACKEND_DIR, ".env")
    root_env = os.path.join(ROOT_DIR, ".env")
    backend_env_example = os.path.join(BACKEND_DIR, ".env.example")
    root_env_example = os.path.join(ROOT_DIR, ".env.example")

    if not os.path.exists(backend_env):
        example_source = backend_env_example if os.path.exists(backend_env_example) else root_env_example
        if os.path.exists(example_source):
            shutil.copy(example_source, backend_env)
            print(f"[CONFIG] Created backend/.env from {os.path.basename(example_source)}")
        else:
            print("[ERROR] Neither backend/.env nor .env.example found!")
            sys.exit(1)

    if not os.path.exists(root_env) and os.path.exists(backend_env):
        shutil.copy(backend_env, root_env)
        print("[CONFIG] Synchronized root .env with backend/.env")

    print("[OK] Configuration files present.")

    # 2. Import application settings & database engine
    try:
        from app.core.config import settings
        from app.core.database import engine, Base
        import app.models # load all models
    except Exception as e:
        print(f"[ERROR] Failed to load application core settings: {e}")
        sys.exit(1)

    masked_db = engine.url.render_as_string(hide_password=True)
    print(f"[CONFIG] Target Database URL: {masked_db}")
    print(f"[CONFIG] Target Redis URL:    {settings.REDIS_URL}")

    # 3. PostgreSQL Health Check with Retry Loop
    print("\nChecking PostgreSQL database connectivity...")
    pg_connected = False
    start_time = time.time()
    last_err = None

    while time.time() - start_time < 15:
        try:
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))
            pg_connected = True
            break
        except Exception as e:
            last_err = e
            time.sleep(1.0)

    if not pg_connected:
        print(f"[ERROR] Could not connect to PostgreSQL within 15s: {last_err}")
        print("  -> Ensure docker container 'postgres-platform' is running on port 5432.")
        print("  -> You can run: docker compose up -d")
        sys.exit(1)

    print("[OK] PostgreSQL is healthy and accepting connections.")

    # 4. Safe Schema Initialization
    try:
        Base.metadata.create_all(bind=engine)
        print("[OK] Database schema verified and up-to-date.")
    except Exception as e:
        print(f"[ERROR] Database schema initialization failed: {e}")
        sys.exit(1)

    # 5. Redis Health Check with Retry Loop
    print("\nChecking Redis connectivity...")
    redis_connected = False
    start_time = time.time()
    last_redis_err = None

    try:
        import redis
        while time.time() - start_time < 10:
            try:
                r = redis.from_url(settings.REDIS_URL, socket_timeout=2.0)
                if r.ping():
                    redis_connected = True
                    break
            except Exception as e:
                last_redis_err = e
                time.sleep(1.0)
    except ImportError:
        print("[WARN] redis package not installed in current environment; skipping direct ping.")
        redis_connected = True

    if not redis_connected:
        print(f"[WARN] Redis connection timed out: {last_redis_err}")
        print("  -> In background/local mode, tasks will fall back to synchronous execution.")
        print("  -> To enable Redis: docker compose up -d redis")
    else:
        print("[OK] Redis is reachable.")

    print("\n" + "=" * 65)
    print("PREFLIGHT PASSED — LOCAL ENVIRONMENT READY TO LAUNCH")
    print("=" * 65)
    return 0

if __name__ == "__main__":
    sys.exit(run_preflight())
