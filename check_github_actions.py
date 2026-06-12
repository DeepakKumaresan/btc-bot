import requests, json

# GitHub personal access token needed to trigger workflow_dispatch
# We'll use the GitHub API to trigger the workflow manually
# First let's check the workflow status via public API

repo = "DeepakKumaresan/btc-bot"
headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

# Check recent workflow runs
r = requests.get(
    f"https://api.github.com/repos/{repo}/actions/runs?per_page=5",
    headers=headers
)
print("Recent workflow runs:")
if r.status_code == 200:
    runs = r.json().get("workflow_runs", [])
    for run in runs:
        print(f"  [{run['status']}] {run['conclusion'] or 'running'} | {run['name']} | {run['created_at']} | {run['html_url']}")
else:
    print(f"  Status {r.status_code}: {r.text[:200]}")
