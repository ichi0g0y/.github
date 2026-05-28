#!/usr/bin/env python3
"""
apply_rulesets.py

rulesets/*.json に保存されたルールセット定義を
ichi0g0y の全リポジトリ（.github 自身を除く）に伝播する。
ruleset-state.json にハッシュを保存し、変更がある場合のみ適用する。

使い方:
    export GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
    python3 apply_rulesets.py [--dry-run] [--force]

オプション:
    --dry-run   実際には変更せず、実行内容を表示するだけ
    --force     ハッシュ一致でも強制的に全リポジトリに再適用する
"""

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# ─── 設定 ──────────────────────────────────────────────────────────────────────

USER_NAME    = "ichi0g0y"
SOURCE_REPO  = f"{USER_NAME}/.github"   # 定義元リポジトリ（除外用）
API_BASE     = "https://api.github.com"
STATE_FILE   = Path(__file__).parent / "ruleset-state.json"
RULESETS_DIR = Path(__file__).parent / "rulesets"   # ルールセット定義JSONディレクトリ

# ──────────────────────────────────────────────────────────────────────────────


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("❌  GITHUB_TOKEN 環境変数が設定されていません。")
        sys.exit(1)
    return token


def api_request(method: str, path: str, token: str, body: dict | None = None) -> dict | list:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "apply-rulesets-script/2.0",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()
        print(f"❌  HTTP {e.code} {method} {url}\n    {body_text}")
        raise


def paginate(path: str, token: str) -> list:
    results = []
    url = f"{API_BASE}{path}?per_page=100"
    while url:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "apply-rulesets-script/2.0",
            },
        )
        with urllib.request.urlopen(req) as resp:
            results.extend(json.loads(resp.read()))
            url = _next_url(resp.headers.get("Link", ""))
    return results


def _next_url(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


# ─── ハッシュ・バージョン管理 ────────────────────────────────────────────────

def compute_hash(ruleset_detail: dict) -> str:
    """ルールセットの中身（name / target / enforcement / rules / conditions）からハッシュを生成する。"""
    snapshot = {
        "name":        ruleset_detail.get("name"),
        "target":      ruleset_detail.get("target"),
        "enforcement": ruleset_detail.get("enforcement"),
        "rules":       sorted(
            [{k: v for k, v in r.items() if k != "ruleset_id"} for r in ruleset_detail.get("rules", [])],
            key=lambda x: json.dumps(x, sort_keys=True),
        ),
        "bypass_actors": ruleset_detail.get("bypass_actors", []),
        "conditions":  ruleset_detail.get("conditions", {}),
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"rulesets": {}, "last_applied": None}


def save_state(state: dict, dry_run: bool):
    if dry_run:
        return
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print(f"💾  ruleset-state.json を更新しました。")


# ─── GitHub API 操作 ──────────────────────────────────────────────────────────

def fetch_source_ruleset_details(token: str) -> list[dict]:
    """rulesets/*.json からルールセット定義を読み込む（APIは使わない）。
    これにより .github リポジトリ自身にルールセットを作成せず、
    ブランチ保護による循環問題を回避する。
    """
    print(f"📋  {RULESETS_DIR}/ からルールセット定義を読み込み中...")
    if not RULESETS_DIR.exists() or not any(RULESETS_DIR.glob("*.json")):
        print(f"   → 定義ファイルが見つかりません。import ジョブを先に実行してください。")
        return []
    details = []
    for f in sorted(RULESETS_DIR.glob("*.json")):
        details.append(json.loads(f.read_text()))
    print(f"   → {len(details)} 件読み込み完了: {[d['name'] for d in details]}")
    return details


def fetch_user_repos(token: str) -> list[dict]:
    print(f"\n📦  {USER_NAME} のリポジトリ一覧を取得中...")
    repos = paginate("/user/repos", token)
    repos = [
        r for r in repos
        if r["owner"]["login"] == USER_NAME
        and not r.get("fork")
        and not r.get("archived")
        and not r.get("private")            # GitHub Free はプライベートリポジトリにルールセット不可
        and r["full_name"] != SOURCE_REPO   # 定義元リポジトリ自身は除外
    ]
    print(f"   → {len(repos)} 件 (fork/archived/private/.github 除外, publicのみ)")
    return repos


def build_repo_payload(detail: dict) -> dict:
    payload = {
        "name":          detail["name"],
        "target":        detail.get("target", "branch"),
        "enforcement":   detail.get("enforcement", "active"),
        "rules":         detail.get("rules", []),
        "bypass_actors": detail.get("bypass_actors", []),
    }
    ref_name = detail.get("conditions", {}).get("ref_name")
    if ref_name:
        payload["conditions"] = {"ref_name": ref_name}
    return payload


def sync_ruleset_to_repo(repo_full: str, payload: dict, token: str, dry_run: bool) -> str:
    name = payload["name"]
    existing_id = None
    try:
        for r in api_request("GET", f"/repos/{repo_full}/rulesets", token):
            if r["name"] == name:
                existing_id = r["id"]
                break
    except Exception:
        pass

    if dry_run:
        action = "更新" if existing_id else "作成"
        return f"[DRY-RUN] {action}: {repo_full} / {name}"

    if existing_id:
        api_request("PUT", f"/repos/{repo_full}/rulesets/{existing_id}", token, payload)
        return f"✅  更新: {repo_full} / {name}"
    else:
        api_request("POST", f"/repos/{repo_full}/rulesets", token, payload)
        return f"✅  作成: {repo_full} / {name}"


# ─── メイン ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="変更せずに実行内容を表示するだけ")
    parser.add_argument("--force",   action="store_true", help="ハッシュ一致でも強制再適用")
    args = parser.parse_args()

    token = get_token()

    if args.dry_run:
        print("🔍  ドライランモード\n")

    # 1. rulesets/*.json からルールセット定義を読み込む
    org_details = fetch_source_ruleset_details(token)
    if not org_details:
        print("ℹ️   適用対象のルールセットがありません。終了します。")
        return

    # 2. ハッシュ計算
    current_hashes = {d["name"]: compute_hash(d) for d in org_details}

    # 3. 保存済みハッシュと比較
    state = load_state()
    saved_hashes = state.get("rulesets", {})

    changed = {
        name: h for name, h in current_hashes.items()
        if args.force or saved_hashes.get(name) != h
    }
    unchanged = [name for name in current_hashes if name not in changed]

    print(f"\n🔍  ハッシュ比較:")
    for name in unchanged:
        print(f"   ✓ 変更なし: {name}")
    for name in changed:
        status = "NEW" if name not in saved_hashes else "CHANGED"
        print(f"   ★ {status}: {name}")

    if not changed:
        print("\n✨  ルールセットに変更なし — 全リポジトリへの適用をスキップします。")
        return

    # 4. 変更があったルールセットのみ適用
    changed_details = [d for d in org_details if d["name"] in changed]
    repos = fetch_user_repos(token)

    print(f"\n🚀  {len(repos)} リポジトリ × {len(changed_details)} ルールセットを適用します...\n")
    errors = []

    for repo in repos:
        for detail in changed_details:
            payload = build_repo_payload(detail)
            try:
                msg = sync_ruleset_to_repo(repo["full_name"], payload, token, args.dry_run)
                print(f"   {msg}")
            except Exception as e:
                err = f"❌  失敗: {repo['full_name']} / {detail['name']} — {e}"
                print(f"   {err}")
                errors.append(err)
            time.sleep(0.3)

    # 5. state.json 更新
    if not errors:
        state["rulesets"] = current_hashes
        state["last_applied"] = datetime.now(timezone.utc).isoformat()
        save_state(state, args.dry_run)
    else:
        print(f"\n⚠️   {len(errors)} 件の失敗があったため state.json は更新しません（次回再試行されます）")

    # 6. サマリー
    total = len(repos) * len(changed_details)
    print(f"\n{'─'*60}")
    print(f"完了: {total - len(errors)}/{total} 件成功 {'(DRY-RUN)' if args.dry_run else ''}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
