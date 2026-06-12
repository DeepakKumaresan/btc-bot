import requests, json, sys

API_KEY = "rnd_OW0AgWUtXy7PAV3SV37b7RAiNMCa"
headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}

print("=" * 60)
print("  RENDER SERVICE STATUS CHECK")
print("=" * 60)

r = requests.get("https://api.render.com/v1/services?limit=20", headers=headers)
services = r.json()
print(f"\nFound {len(services)} service(s):\n")

for item in services:
    svc = item["service"]
    details = svc["serviceDetails"]
    name     = svc["name"]
    region   = details.get("region", "unknown")
    suspended= svc["suspended"]
    url      = details.get("url", "no-url")
    svc_id   = svc["id"]
    print(f"  Name     : {name}")
    print(f"  ID       : {svc_id}")
    print(f"  Region   : {region}")
    print(f"  Suspended: {suspended}")
    print(f"  URL      : {url}")
    print()

    # Check latest deploy status
    dr = requests.get(f"https://api.render.com/v1/services/{svc_id}/deploys?limit=1", headers=headers)
    deploys = dr.json()
    if deploys:
        dep = deploys[0]["deploy"]
        print(f"  Latest Deploy: {dep['status']} (created {dep['createdAt']})")
    print("-" * 60)

print("\nPinging each service URL...")
for item in services:
    svc = item["service"]
    url = svc["serviceDetails"].get("url", "")
    if url:
        try:
            pr = requests.get(url + "/", timeout=20)
            print(f"  {url}/ => HTTP {pr.status_code}: {pr.text[:80]}")
        except Exception as e:
            print(f"  {url}/ => FAILED: {e}")
