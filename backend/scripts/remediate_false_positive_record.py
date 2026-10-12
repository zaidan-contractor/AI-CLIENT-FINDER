import sys
from app.core.database import SessionLocal
from sqlalchemy import text

def remediate():
    db = SessionLocal()
    target_id = "2521b505-8b28-46ee-b785-aa494ec58c07"
    update_stmt = text("""
        UPDATE outbound_messages
        SET status = 'FALSE_POSITIVE_SIMULATED_TRANSMISSION',
            transport_mode = 'MOCK',
            gate_failure_reason = 'AUDIT_REMEDIATION: Simulated success returned by SMTP_SANDBOX bug without real wire transmission. Marked as FALSE_POSITIVE_SIMULATED_TRANSMISSION. Audit history preserved.'
        WHERE id = :target_id
        RETURNING id, status, transport_mode, gate_failure_reason;
    """)
    res = db.execute(update_stmt, {"target_id": target_id})
    row = res.fetchone()
    db.commit()
    if row:
        print(f"SUCCESS: Remediated record {row[0]} -> status={row[1]}, transport_mode={row[2]}")
        print(f"Reason: {row[3]}")
    else:
        print(f"Record {target_id} not found in database.")
    db.close()

if __name__ == "__main__":
    remediate()
