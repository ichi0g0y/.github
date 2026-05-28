#!/usr/bin/env python3
"""
apply_rulesets.py - nantokaworks org ルールセットを ichi0g0y の全リポジトリに同期する
ruleset-state.json でハッシュ管理し、変更時のみ適用する
"""
import argparse, hashlib, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

ORG_NAME   = "nantokaworks"
USER_NAME  = "ichi0g0y"
API_BASE   = "https://api.github.com"
STATE_FILE = Path(__file__).parent / "ruleset-state.json"

def get_token():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token: print("GITHUB_TOKEN not set"); sys.exit(1)
    return token

def api_request(method, path, token, body=None):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
        "User-Agent": "apply-rulesets/2.0"})
    try:
        with urllib.request.urlopen(req) as resp: return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {method} {url}\n{e.read().decode()}"); raise

def paginate(path, token):
    results, url = [], f"{API_BASE}{path}?per_page=100"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "apply-rulesets/2.0"})
        with urllib.request.urlopen(req) as resp:
            results.extend(json.loads(resp.read()))
            link = resp.headers.get("Link", "")
            url = next((p.split(";")[0].strip().strip("<>") for p in link.split(",") if 'rel="next"' in p), None)
    return results

def compute_hash(detail):
    snap = {"name": detail.get("name"), "target": detail.get("target"),
        "enforcement": detail.get("enforcement"),
        "rules": sorted([{k:v for k,v in r.items() if k!="ruleset_id"} for r in detail.get("rules",[])],
            key=lambda x: json.dumps(x, sort_keys=True)),
        "bypass_actors": detail.get("bypass_actors",[]), "conditions": detail.get("conditions",{})}
    return hashlib.sha256(json.dumps(snap, sort_keys=True).encode()).hexdigest()

def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"rulesets":{}, "last_applied":None}

def save_state(state, dry_run):
    if dry_run: return
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print("ruleset-state.json updated")

def fetch_org_rulesets(token):
    print(f"Fetching {ORG_NAME} rulesets...")
    summaries = paginate(f"/orgs/{ORG_NAME}/rulesets", token)
    details = []
    for rs in summaries:
        if rs.get("target") == "repository": print(f"  skip {rs['name']}"); continue
        details.append(api_request("GET", f"/orgs/{ORG_NAME}/rulesets/{rs['id']}", token))
    print(f"  {len(details)} rulesets fetched")
    return details

def fetch_user_repos(token):
    print(f"Fetching {USER_NAME} repos...")
    repos = paginate("/user/repos", token)
    repos = [r for r in repos if r["owner"]["login"]==USER_NAME and not r.get("fork") and not r.get("archived")]
    print(f"  {len(repos)} repos (public+private, excl fork/archived)")
    return repos

def build_payload(detail):
    p = {"name":detail["name"],"target":detail.get("target","branch"),
        "enforcement":detail.get("enforcement","active"),
        "rules":detail.get("rules",[]),"bypass_actors":detail.get("bypass_actors",[])}
    ref = detail.get("conditions",{}).get("ref_name")
    if ref: p["conditions"] = {"ref_name": ref}
    return p

def sync_to_repo(repo_full, payload, token, dry_run):
    name = payload["name"]
    existing_id = None
    try:
        for r in api_request("GET", f"/repos/{repo_full}/rulesets", token):
            if r["name"] == name: existing_id = r["id"]; break
    except: pass
    if dry_run: return f"[DRY] {'update' if existing_id else 'create'}: {repo_full}/{name}"
    if existing_id:
        api_request("PUT", f"/repos/{repo_full}/rulesets/{existing_id}", token, payload)
        return f"updated: {repo_full}/{name}"
    else:
        api_request("POST", f"/repos/{repo_full}/rulesets", token, payload)
        return f"created: {repo_full}/{name}"

def main():
    p = argparse.ArgumentParser(); p.add_argument("--dry-run",action="store_true"); p.add_argument("--force",action="store_true")
    args = p.parse_args()
    token = get_token()
    org_details = fetch_org_rulesets(token)
    if not org_details: print("No rulesets found."); return
    current = {d["name"]: compute_hash(d) for d in org_details}
    state = load_state(); saved = state.get("rulesets",{})
    changed = {n:h for n,h in current.items() if args.force or saved.get(n)!=h}
    unchanged = [n for n in current if n not in changed]
    for n in unchanged: print(f"  unchanged: {n}")
    for n in changed: print(f"  {'NEW' if n not in saved else 'CHANGED'}: {n}")
    if not changed: print("No changes, skipping."); return
    repos = fetch_user_repos(token)
    changed_details = [d for d in org_details if d["name"] in changed]
    print(f"Applying {len(changed_details)} rulesets to {len(repos)} repos...")
    errors = []
    for repo in repos:
        for detail in changed_details:
            try: print(f"  {sync_to_repo(repo['full_name'], build_payload(detail), token, args.dry_run)}")
            except Exception as e: errors.append(str(e)); print(f"  FAIL: {e}")
            time.sleep(0.3)
    if not errors:
        state["rulesets"] = current; state["last_applied"] = datetime.now(timezone.utc).isoformat()
        save_state(state, args.dry_run)
    else: print(f"{len(errors)} errors - state not updated"); sys.exit(1)
    print(f"Done: {len(repos)*len(changed_details)-len(errors)}/{len(repos)*len(changed_details)} ok")

if __name__ == "__main__": main()
