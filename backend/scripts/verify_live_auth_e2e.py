import urllib.request
import json
import sys

def main():
    print("=== 1. VERIFY FRONTEND SERVER ON PORT 5500 ===")
    with urllib.request.urlopen("http://127.0.0.1:5500/") as resp:
        html = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" in html
        assert "Opportunity Intelligence" in html
        assert 'id="login-form"' in html
        print(f"Frontend HTML loaded successfully: {resp.status} ({len(html)} bytes)")

    print("\n=== 2. VERIFY WRONG PASSWORD REJECTION ===")
    try:
        login_data = json.dumps({"email": "admin@omni.com", "password": "WrongPassword123!"}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8000/api/v1/auth/login",
            data=login_data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req)
        print("ERROR: Wrong password succeeded unexpectedly!")
        sys.exit(1)
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode("utf-8"))
        assert e.code == 401
        assert err["detail"] == "Incorrect email or password."
        print(f"Wrong password rejected with HTTP 401: {err}")

    print("\n=== 3. VERIFY VALID DEMO LOGIN (admin@omni.com / AdminPass123!) ===")
    login_data = json.dumps({"email": "admin@omni.com", "password": "AdminPass123!"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        token_resp = json.loads(resp.read().decode("utf-8"))
        token = token_resp["access_token"]
        assert token_resp["token_type"] == "bearer"
        assert token_resp["user_id"] == "usr_demo_admin"
        assert token_resp["organization_id"] == "org_demo"
        assert token_resp["active_workspace_id"] == "ws_demo_1"
        assert token_resp["role"] == "WorkspaceAdmin"
        print(f"Login 200 OK: User={token_resp['user_id']}, Role={token_resp['role']}, ActiveWorkspace={token_resp['active_workspace_id']}")

    auth_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    print("\n=== 4. VERIFY /me CONTEXT ===")
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1/auth/me", headers=auth_headers)
    with urllib.request.urlopen(req) as resp:
        me = json.loads(resp.read().decode("utf-8"))
        assert me["email"] == "admin@omni.com"
        assert me["is_active"] is True
        print(f"User context (/me): {me['email']}, role={me['role']}, org={me['organization_id']}, ws={me['active_workspace_id']}")

    print("\n=== 5. VERIFY ACCESSIBLE WORKSPACES ===")
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1/auth/workspaces", headers=auth_headers)
    with urllib.request.urlopen(req) as resp:
        workspaces = json.loads(resp.read().decode("utf-8"))
        assert len(workspaces) >= 2
        print(f"Available workspaces ({len(workspaces)}): {[(w['id'], w['name']) for w in workspaces]}")

    print("\n=== 6. VERIFY TENANT BUSINESSES IN DEMO WORKSPACE ===")
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1/businesses?workspace_id=ws_demo_1", headers=auth_headers)
    with urllib.request.urlopen(req) as resp:
        biz_list = json.loads(resp.read().decode("utf-8"))
        print(f"Fetched {len(biz_list)} businesses in ws_demo_1: {[b['name'] for b in biz_list]}")

    print("\n=================================================")
    print("ALL LIVE END-TO-END VERIFICATIONS PASSED 100%!")
    print("=================================================")

if __name__ == "__main__":
    main()
