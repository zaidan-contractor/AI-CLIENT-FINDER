import requests

BASE = 'http://127.0.0.1:8000/api/v1'

print("Starting live API client test against running server...")

# 1. Login
resp = requests.post(f'{BASE}/auth/login', json={'email': 'admin@omni.com', 'password': 'AdminPass123!'})
assert resp.status_code == 200, f'Login failed: {resp.text}'
data = resp.json()
token = data['access_token']
headers = {'Authorization': f'Bearer {token}', 'X-Workspace-Id': 'ws_demo_1'}
print('[PASS] 1. Login successful. JWT token received.')

# 2. Get businesses in active workspace
b_resp = requests.get(f'{BASE}/businesses', headers=headers)
assert b_resp.status_code == 200
biz_list = b_resp.json()
names = [b["name"] for b in biz_list]
print(f'[PASS] 2. Real businesses returned ({len(biz_list)}): {names}')
assert 'Austin Family Health' in names

# 3. Switch workspace and verify isolation
s_resp = requests.post(f'{BASE}/auth/switch-workspace', json={'workspace_id': 'ws_demo_2'}, headers=headers)
assert s_resp.status_code == 200
ws2_token = s_resp.json()['access_token']
ws2_headers = {'Authorization': f'Bearer {ws2_token}', 'X-Workspace-Id': 'ws_demo_2'}
ws2_biz = requests.get(f'{BASE}/businesses', headers=ws2_headers).json()
ws2_names = [b["name"] for b in ws2_biz]
print(f'[PASS] 3. Workspace switched to ws_demo_2. Businesses in ws2: {ws2_names}. Workspace isolation verified.')
assert 'Austin Family Health' not in ws2_names

# 4. Get claims
c_resp = requests.get(f'{BASE}/claims', headers=headers)
assert c_resp.status_code == 200
claims = c_resp.json()
claim_texts = [c["claim_text"][:35] for c in claims]
print(f'[PASS] 4. Real claims returned ({len(claims)}): {claim_texts}')

# 5. Get inbound
i_resp = requests.get(f'{BASE}/inbound/messages', headers=headers)
assert i_resp.status_code == 200
inbound = i_resp.json()
senders = [i["sender_email"] for i in inbound]
print(f'[PASS] 5. Real inbound returned ({len(inbound)}): {senders}')
assert any(i['classified_intent'] == 'HOT_DEAL' for i in inbound)

# 6. Get recommendations
r_resp = requests.get(f'{BASE}/recommendations', headers=headers)
assert r_resp.status_code == 200
recs = r_resp.json()
headlines = [r["headline"][:40] for r in recs]
print(f'[PASS] 6. Real recommendations returned ({len(recs)}): {headlines}')

# 7. Toggle Emergency Kill Switch
k_on = requests.post(f'{BASE}/system/kill-switch', json={'active': True})
assert k_on.status_code == 200
h_on = requests.get(f'{BASE}/health').json()
assert h_on['emergency_kill_switch'] is True
print('[PASS] 7. Emergency Kill Switch mutated to ON and persisted.')

# 8. Toggle Kill Switch OFF
k_off = requests.post(f'{BASE}/system/kill-switch', json={'active': False})
assert k_off.status_code == 200
h_off = requests.get(f'{BASE}/health').json()
assert h_off['emergency_kill_switch'] is False
print('[PASS] 8. Emergency Kill Switch mutated back to OFF and persisted.')

print("\nALL LIVE FRONTEND BACKEND API INTEGRATIONS PASSED!")
