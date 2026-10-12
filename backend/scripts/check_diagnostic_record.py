from app.core.database import SessionLocal
from sqlalchemy import text

def check():
    db = SessionLocal()
    row = db.execute(text("SELECT id, recipient_email, status, transport_mode, provider_message_id, gate_passed_count, sent_at FROM outbound_messages WHERE id = 'f8a80d79-b138-4396-b4d2-9e9cfab66d42'")).fetchone()
    print("CHECKED TRANSMITTED RECORD:", row)
    db.close()

if __name__ == "__main__":
    check()
