#!/usr/bin/env python3
"""
apply_workflows.py

ichi0g0y/.github の .github/workflows/ に保存されたワークフロー定義を
全リポジトリ（.github 自身を除く）の .github/workflows/ に伝播する。

sync-rulesets.yml など伝播不要なファイルは SKIP_FILES で除外。

使い方:
    export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
    python3 apply_workflows.py [--dry-run]
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error

USER_NAME      = "ichi0g0y"
SOURCE_REPO    = f"{USER_NAME}/.github"
API_BASE       = "https://api.github.com"
WORKFLOWS_PATH = ".github/workflows"
# 伝播しないファイル（このリポジトリ専用のメタワークフロー）
SKIP_FILES     = {"sync-rulesets.yml"}


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("❌  GITHUB_TOKEN 環境変数が設定されていません。")
        sys.exit(1)
    return token


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "apply-workflows-script/1.0",
    }


def api_get(path: str, token: str):
    url = f"{API_BASE}{path}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        code = e.code
        e.read()  # drain
        return None, code


def api_put(path: str, token: str, body: dict):
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT", headers=_headers(token))
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"❌  HTTP {e.code} PUT {url}\n    {body_text}")
        raise


def paginate(path: str, token: str) -> list:
    results = []
    url = f"{API_BASE}{path}?per_page=100"
    while url:
        req = urllib.request.Request(url, headers=_headers(token))
        with urllib.request.urlopen(req) as resp:
            results.extend(json.loads(resp.read()))
            link = resp.headers.get("Link", "")
            url = next(
                (p.split(";")[0].strip().strip("<>")
                 for p in link.split(",") if 'rel="next"' in p),
                None,
            )
    return results


def fetch_source_workflows(token: str) -> list:
    print(f"📋  {SOURCE_REPO}/{WORKFLOWS_PATH}/ からワークフロー一覧を取得中...")
    data, err = api_get(f"/repos/{SOURCE_REPO}/contents/{WORKFLOWS_PATH}", token)
    if data is None:
        print(f"   → 取得失敗: HTTP {err}")
        return []

    workflows = []
    for item in data:
        if item["type"] != "file" or not item["name"].endswith((".yml", ".yaml")):
            continue
        if item["name"] in SKIP_FILES:
            print(f"   → スキップ: {item['name']}")
            continue
        detail, err2 = api_get(
            f"/repos/{SOURCE_REPO}/contents/{WORKFLOWS_PATH}/{item['name']}", token
        )
        if detail is None:
            print(f"   → 取得失敗: {item['name']}")
            continue
        content = base64.b64decode(detail["content"]).decode()
        workflows.append({
            "name": item["name"],
            "content": content,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        })

    print(f"   → {len(workflows)} 件: {[w['name'] for w in workflows]}")
    return workflows


def fetch_user_repos(token: str) -> list:
    print(f"\n📦  {USER_NAME} のリポジトリ一覧を取得中...")
    repos = paginate("/user/repos", token)
    repos = [
        r for r in repos
        if r["owner"]["login"] == USER_NAME
        and not r.get("fork")
        and not r.get("archived")
        and not r.get("private")
        and r["full_name"] != SOURCE_REPO
    ]
    print(f"   → {len(repos)} 件 (fork/archived/private/.github 除外, publicのみ)")
    return repos


def sync_workflow_to_repo(repo_full: str, wf: dict, token: str, dry_run: bool) -> str:
    name = wf["name"]
    path = f"{WORKFLOWS_PATH}/{name}"

    existing_sha = None
    detail, err = api_get(f"/repos/{repo_full}/contents/{path}", token)
    if detail is not None:
        existing_sha = detail.get("sha")
        existing_content = base64.b64decode(detail["content"]).decode()
        if hashlib.sha256(existing_content.encode()).hexdigest() == wf["sha256"]:
            return f"✓ 変更なし: {repo_full} / {name}"
    elif err != 404:
        return f"❌  取得失敗: {repo_full} / {name} (HTTP {err})"

    if dry_run:
        action = "更新" if existing_sha else "作成"
        return f"[DRY-RUN] {action}: {repo_full} / {name}"

    body = {
        "message": f"ci: sync {name} from ichi0g0y/.github",
        "content": base64.b64encode(wf["content"].encode()).decode(),
    }
    if existing_sha:
        body["sha"] = existing_sha

    api_put(f"/repos/{repo_full}/contents/{path}", token, body)
    action = "更新" if existing_sha else "作成"
    return f"✅  {action}: {repo_full} / {name}"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="変更せずに実行内容を表示")
    args = parser.parse_args()

    token = get_token()
    if args.dry_run:
        print("🔍  ドライランモード\n")

    workflows = fetch_source_workflows(token)
    if not workflows:
        print("ℹ️   伝播対象のワークフローがありません。終了します。")
        return

    repos = fetch_user_repos(token)
    print(f"\n🚀  {len(repos)} リポジトリ × {len(workflows)} ワークフローを適用します...\n")

    errors = []
    for repo in repos:
        for wf in workflows:
            try:
                msg = sync_workflow_to_repo(repo["full_name"], wf, token, args.dry_run)
                print(f"   {msg}")
            except Exception as e:
                err_msg = f"❌  失敗: {repo['full_name']} / {wf['name']} — {e}"
                print(f"   {err_msg}")
                errors.append(err_msg)
            time.sleep(0.3)

    total = len(repos) * len(workflows)
    print(f"\n{'─'*60}")
    print(f"完了: {total - len(errors)}/{total} 件成功 {'(DRY-RUN)' if args.dry_run else ''}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
