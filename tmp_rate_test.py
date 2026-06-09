import urllib.request, urllib.error, json, time

payload = {'email': 'test-p2@example.com', 'password': 'password'}
data = json.dumps(payload).encode('utf-8')
url = 'https://scorelib-backend.onrender.com/api/auth/login'
headers = {'Content-Type': 'application/json'}
for i in range(1, 11):
    print('REQUEST', i)
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        print('STATUS', resp.status)
        print('\n'.join(f'{k}: {v}' for k, v in resp.getheaders() if k.lower() in ['content-security-policy','referrer-policy','strict-transport-security','x-content-type-options','x-frame-options','retry-after']))
        print(resp.read().decode(errors='replace'))
    except urllib.error.HTTPError as e:
        print('STATUS', e.code)
        print('\n'.join(f'{k}: {v}' for k, v in e.headers.items() if k.lower() in ['content-security-policy','referrer-policy','strict-transport-security','x-content-type-options','x-frame-options','retry-after']))
        print(e.read().decode(errors='replace'))
    time.sleep(0.2)
