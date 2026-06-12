import requests, json

headers = {
    'Authorization': 'Bearer rnd_OW0AgWUtXy7PAV3SV37b7RAiNMCa',
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

payload = {
    'type': 'web_service',
    'name': 'btc-signal-bot-v3',
    'ownerId': 'tea-cvddkhlrie7s739o1o90',
    'repo': 'https://github.com/DeepakKumaresan/btc-bot',
    'branch': 'master',
    'autoDeploy': 'yes',
    'serviceDetails': {
        'env': 'python',
        'region': 'frankfurt',
        'plan': 'free',
        'envSpecificDetails': {
            'buildCommand': 'pip install ccxt pandas numpy ta requests scikit-learn',
            'startCommand': 'python -u btc_apex_v5.py'
        },
        'envVars': [
            {'key': 'TG_TOKEN', 'value': '8775276870:AAGABvQ6PwtRgGPNbk3V4YX_A0eVXxpiWyo'},
            {'key': 'TG_CHAT',  'value': '998659643'},
            {'key': 'PORT',     'value': '10000'}
        ],
        'numInstances': 1,
        'pullRequestPreviewsEnabled': 'no'
    }
}

r = requests.post('https://api.render.com/v1/services', headers=headers, json=payload)
print('Status:', r.status_code)
data = r.json()
print(json.dumps(data, indent=2))
