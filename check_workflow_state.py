import requests, json

repo = "DeepakKumaresan/btc-bot"
headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# Check workflow list and enabled/disabled state
r = requests.get(f"https://api.github.com/repos/{repo}/actions/workflows", headers=headers)
print("Workflows:")
if r.status_code == 200:
    for wf in r.json().get("workflows", []):
        print(f"  ID      : {wf['id']}")
        print(f"  Name    : {wf['name']}")
        print(f"  State   : {wf['state']}")   # active / disabled_manually / disabled_inactivity
        print(f"  Path    : {wf['path']}")
        print(f"  URL     : {wf['html_url']}")
        print()
else:
    print(f"Status {r.status_code}: {r.text[:300]}")
