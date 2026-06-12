import requests, json

headers = {
    'Authorization': 'Bearer rnd_OW0AgWUtXy7PAV3SV37b7RAiNMCa',
    'Accept': 'application/json'
}
svc_id = 'srv-d8dfp5kp3tds73fieuvg'

r = requests.get(f'https://api.render.com/v1/services/{svc_id}/events?limit=50', headers=headers)
if r.status_code == 200:
    for item in r.json():
        evt = item['event']
        ts = evt.get('timestamp')
        etype = evt.get('type')
        details = evt.get('details', {})
        print(f"{ts} | {etype} | {details}")
else:
    print(f"Error {r.status_code}: {r.text}")
