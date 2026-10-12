# Production Setup & External Service Validation Guide

> **Current System Status:**  
> `PRODUCTION NOT VALIDATED — EXTERNAL TESTING INCOMPLETE`  
>  
> The codebase architecture, 16-gate outbound pipeline, database models, worker queues, and security guardrails have passed 97/97 internal automated tests and offline verification. External live integrations have **not** been connected with real production credentials yet.

---

## 1. Complete Environment Configuration Guide

Copy `.env.example` to `.env` in the `backend/` directory:
```bash
cp .env.example backend/.env
```

> [!CAUTION]
> **CRITICAL SECURITY INVARIANT**:  
> **NEVER** commit `.env` or place real production credentials in Git, source code, tests, documentation, or application logs. `.env` is ignored in `.gitignore`.

### Required Environment Variables

| Variable | Required In Prod? | Description & Constraints | Example Value |
| :--- | :--- | :--- | :--- |
| `ENVIRONMENT` | **YES** | Execution environment. When set to `production`, SQLite is strictly rejected, and all security guardrails are enforced. | `production` (or `development`) |
| `DEBUG` | **YES** | Must be `false` in production. | `false` |
| `DATABASE_URL` | **YES** | PostgreSQL connection URL. Must begin with `postgresql://` or `postgresql+psycopg2://`. SQLite is prohibited in production. | `postgresql://platform_user:Secr3tP@ss!@localhost:5432/opportunity_platform` |
| `REDIS_URL` | **YES** | Redis / Valkey connection URL for background jobs, rate limits, and dispatch queues. Must begin with `redis://` or `rediss://`. | `redis://localhost:6379/0` |
| `JWT_SECRET_KEY` | **YES** | Secret key for signing HS256 auth tokens. Must be a cryptographically random string (min 32 bytes). | `openssl rand -hex 32` |
| `JWT_ALGORITHM` | **YES** | Algorithm for JWT validation. Must remain `HS256`. | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | **YES** | JWT token lifetime in minutes. | `1440` (24 hours) |
| `ENCRYPTION_MASTER_KEY` | **YES** | 32-byte URL-safe base64 key for AES-256-GCM envelope encryption of sender credentials. | `python -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"` |
| `SUPPRESSION_HMAC_SECRET_KEY` | **YES** | Cryptographic secret for generating privacy-preserving SHA256 suppression hashes and RFC 8058 unsubscribe tokens. | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `INBOUND_WEBHOOK_SECRET` | **YES** | HMAC-SHA256 secret for verifying inbound email provider webhooks. Rejects signatures older than 300s. | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SMTP_HOST` | **Conditional** | Outbound SMTP host (if using SMTP transport). | `smtp.mailgun.org` or `email-smtp.us-east-1.amazonaws.com` |
| `SMTP_PORT` | **Conditional** | Outbound SMTP port (usually 587 or 465). | `587` |
| `SMTP_USERNAME` | **Conditional** | Outbound SMTP username. | `postmaster@mg.yourdomain.com` |
| `SMTP_PASSWORD` | **Conditional** | Outbound SMTP password. | `your-smtp-password` |
| `SMTP_USE_TLS` | **Conditional** | Use STARTTLS for SMTP connections. | `true` |
| `EMAIL_API_PROVIDER` | **Conditional** | Outbound transactional API provider: `resend` or `postmark`. | `resend` |
| `EMAIL_API_KEY` | **Conditional** | API key for Resend or Server Token for Postmark. | `re_123456789...` |
| `LLM_PROVIDER` | **Conditional** | LLM provider backend: `openai`, `anthropic`, `gemini`, `deepseek`, or `ollama`. | `openai` |
| `LLM_API_KEY` | **Conditional** | API key for external LLM inference. | `sk-...` |
| `LLM_API_BASE_URL` | Optional | Custom base URL (for Azure OpenAI, Ollama, vLLM, or self-hosted models). | `https://api.openai.com/v1` |
| `LLM_MODEL_NAME` | Optional | Model identifier. Defaults to `gpt-4o-mini`. | `gpt-4o-mini` |
| `VITE_API_URL` | Frontend | Base URL of backend API for Vite frontend client. | `http://localhost:8000` |

---

## 2. PostgreSQL Setup & Migration Guide

Production and external-service validation **must** use PostgreSQL. SQLite is strictly rejected when `ENVIRONMENT=production`.

### Option A: Running PostgreSQL via Docker (Recommended for Local Validation)
```bash
docker run --name postgres-platform \
  -e POSTGRES_DB=opportunity_platform \
  -e POSTGRES_USER=platform_user \
  -e POSTGRES_PASSWORD=platform_password_123 \
  -p 5432:5432 \
  -d postgres:16-alpine
```

### Option B: Running PostgreSQL via Native Host / Managed Cloud
1. Connect to PostgreSQL using `psql` as a superuser:
```sql
CREATE DATABASE opportunity_platform;
CREATE USER platform_user WITH ENCRYPTED PASSWORD 'platform_password_123';
GRANT ALL PRIVILEGES ON DATABASE opportunity_platform TO platform_user;
\c opportunity_platform
GRANT ALL ON SCHEMA public TO platform_user;
```

### Configure Connection String in `backend/.env`
```env
DATABASE_URL=postgresql://platform_user:platform_password_123@localhost:5432/opportunity_platform
```

### Run Migrations & Verify
Navigate to `backend/` and run Alembic migrations:
```bash
cd backend
alembic upgrade head
```

Verify connection:
```bash
python -c "from app.core.database import SessionLocal; db = SessionLocal(); print('PostgreSQL connected and verified successfully'); db.close()"
```

---

## 3. Redis / Valkey Setup Guide

Production dispatch requires a real Redis or Valkey server. The in-process synchronous runner is reserved for offline unit testing only.

### Option A: Running Redis via Docker
```bash
docker run --name redis-platform \
  -p 6379:6379 \
  -d redis:7-alpine
```

### Configure Connection String in `backend/.env`
```env
REDIS_URL=redis://localhost:6379/0
```

### Verify Redis Connectivity
```bash
python -c "import redis; r = redis.from_url('redis://localhost:6379/0'); r.ping(); print('Redis connection verified')"
```

### Start Background Queue Worker
In a dedicated terminal window:
```bash
cd backend
python -m app.workers.outbound_worker
```
The worker will listen on `outbound_dispatch`, processing queued messages through the 16-gate engine and real email transports.

---

## 4. Email Provider Setup (Controlled Operator Mailbox Validation)

> [!IMPORTANT]
> **VALIDATION RULE**:  
> The first live test **MUST** use a controlled mailbox owned and operated by you (e.g., your own test email address).  
> **DO NOT** send prospect outreach during validation.

The system supports three email transports:
1. **SMTP** (Generic SMTP, Amazon SES, Mailgun, SendGrid)
2. **Resend API**
3. **Postmark API**

### Configuring SMTP (Example: Mailgun or Amazon SES)
Add to `backend/.env`:
```env
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=postmaster@mg.yourdomain.com
SMTP_PASSWORD=your_real_smtp_password
SMTP_USE_TLS=true
```

### Configuring Resend API
Add to `backend/.env`:
```env
EMAIL_API_PROVIDER=resend
EMAIL_API_KEY=re_1234567890abcdef...
```

### Configuring Postmark API
Add to `backend/.env`:
```env
EMAIL_API_PROVIDER=postmark
EMAIL_API_KEY=2523e4b1-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

### Live Validation Proof Sequence
When configured, triggering a test dispatch executes the complete pipeline:
```text
API Dispatch Request (/api/v1/outbound/dispatch)
  ↓
Enqueued to Redis Queue ('outbound_dispatch')
  ↓
Picked up by Worker (app.workers.outbound_worker)
  ↓
OutboundDispatchEngine: 16 Sequential Gates Evaluated
  ↓ (Gate 14 Atomic Pre-Transmission Suppression Check)
Provider Transport Resolution (SMTP / Resend / Postmark)
  ↓
Wire Transmission to Operator Mailbox
  ↓
Provider Message ID Captured (e.g., <msg_123@smtp>)
  ↓
Status Persisted as 'TRANSMITTED' in PostgreSQL
```

---

## 5. LLM Provider Setup & Diagnostic Testing

The system features a model-agnostic inference layer (`app.services.llm_client.LLMClient`). In offline mode, a deterministic rule-based fallback is used. For production validation, a live external LLM must be connected.

### Configuring OpenAI
Add to `backend/.env`:
```env
LLM_PROVIDER=openai
LLM_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxx
LLM_API_BASE_URL=https://api.openai.com/v1
LLM_MODEL_NAME=gpt-4o-mini
```

### Configuring Anthropic / Open-Source / Local LLMs (Ollama)
For Anthropic or local OpenAI-compatible endpoints:
```env
LLM_PROVIDER=openai
LLM_API_KEY=ollama
LLM_API_BASE_URL=http://localhost:11434/v1
LLM_MODEL_NAME=llama3.1:8b
```

### Live Diagnostic Request Verification
A harmless diagnostic request verifies:
- Live network connectivity and HTTPS TLS handshake
- Strict JSON structured output adherence
- Token usage tracking and latency metrics
- Model metadata recording
- Timeout handling (default: 15s)
- Resistance to prompt-injection payloads
- Strict adherence to the advisory-only authorization boundary (LLM output is treated as untrusted suggestions, never executing mutations directly).

> [!NOTE]
> The rule-based fallback will report `SERVICE NOT CONFIGURED` in the validation script. It will **never** be misreported as a live LLM integration.

---

## 6. Production Validation Script

Run the automated production validation suite to inspect all external service connections:
```bash
cd backend
python scripts/run_production_validation.py
```

### Status Meaning Table

| Status Label | Description |
| :--- | :--- |
| `REAL SERVICE TEST PASSED` | Real external service reached over network and passed functional contract test. |
| `REAL SERVICE CONNECTED` | Service connection verified, but full transaction not executed. |
| `SERVICE NOT CONFIGURED` | Environment variable or credentials not present in `.env`. System using offline fallback or disabled. |
| `SERVICE CONNECTION FAILED` | Credentials or endpoint configured, but connection was refused, timed out, or rejected. |
| `SERVICE TEST FAILED` | Connected to service, but functional or security assertions failed. |

---

## 7. Final Smoke-Test Commands

Run these exact commands across separate terminal panes to launch the complete platform for production validation:

### 1. PostgreSQL (Docker or Host)
```bash
docker run --name postgres-platform -e POSTGRES_DB=opportunity_platform -e POSTGRES_USER=platform_user -e POSTGRES_PASSWORD=platform_password_123 -p 5432:5432 -d postgres:16-alpine
```

### 2. Redis (Docker or Host)
```bash
docker run --name redis-platform -p 6379:6379 -d redis:7-alpine
```

### 3. Backend Database Migrations & API Server
```bash
cd backend
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Outbound Worker Process
```bash
cd backend
python -m app.workers.outbound_worker
```

### 5. Frontend Client
```bash
cd frontend
npm install
npm run dev -- --port 5173
```

### 6. Live Validation Suite
```bash
cd backend
python scripts/run_production_validation.py
```

---

## 8. Security Audit Checklist

Before placing any real credentials into your `.env` file, verify the following:

- [x] `.env` is listed in `.gitignore` and has never been committed to Git.
- [x] Credentials are encrypted at rest via AES-256-GCM envelope encryption (`ENCRYPTION_MASTER_KEY`).
- [x] Credentials are decrypted in-memory only at transmission time and are never logged.
- [x] Redis queue payloads contain only IDs (`outbound_message_id`, `workspace_id`), never credentials or sensitive tokens.
- [x] LLM prompts receive sanitized business evidence, never API keys, database credentials, or auth secrets.
- [x] Frontend API responses redact encrypted credentials and private keys.
- [x] Production startup (`ENVIRONMENT=production`) strictly fails if `DATABASE_URL` is SQLite or if cryptographic secrets use default values.
