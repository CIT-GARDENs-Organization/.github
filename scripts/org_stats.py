#!/usr/bin/env python3
# scripts/org_stats.py
"""
GARDENs 用: 組織の全リポジトリから
1. 最近動いたリポジトリ
2. 言語サマリ
3. コントリビュータランキング
4. 衛星カテゴリ別のリポジトリ一覧
を生成して README.md の所定ブロックを書き換える。
さらに言語サマリの SVG グラフを assets/langs.svg に出力する。

GitHub Actions から実行されることを想定
"""

import os
import sys
import json
from datetime import datetime, timezone
from urllib import request
from collections import defaultdict, Counter

# matplotlib は Actions でインストールする
import matplotlib
matplotlib.use("Agg")  # GUIなし環境
import matplotlib.pyplot as plt

# ====== 設定 ======
ORG_NAME = os.environ.get("ORG_NAME", "").strip()
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
README_PATH = "README.md"
BLOCK_START = "<!-- ORG-STATS:START -->"
BLOCK_END = "<!-- ORG-STATS:END -->"
LANG_SVG_PATH = "assets/langs.svg"

# 衛星ごとの判定に使うキーワード
SATELLITE_GROUPS = {
    "YOMOGI": ["yomogi", "YOMOGI", "ymg", "YMG"],
    "KASHIWA": ["kashiwa", "KASHIWA", "ksh", "KSH"],
    "SAKURA": ["sakura", "SAKURA", "skr", "SKR"],
    "BOTAN": ["botan", "BOTAN", "btn", "BTN"],
    "MOMIJI": ["momiji", "MOMIJI", "mmj", "MMJ"]
}
# その他に分類するキー
OTHER_GROUP = "OTHERS"


# ====== API 基本関数 ======
def github_api(url: str):
    """
    GitHub REST API を叩いて JSON を返す。
    認証は環境変数の TOKEN を使用。
    """
    if not TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set")

    req = request.Request(url)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    with request.urlopen(req) as resp:
        data = resp.read()
    return json.loads(data.decode("utf-8"))


def fetch_all_repos(org: str):
    """
    組織の全リポジトリをページングして取得する。
    public/private 両方を見る場合は権限のあるTOKENが必要。
    """
    repos = []
    page = 1
    per_page = 100
    while True:
        url = f"https://api.github.com/orgs/{org}/repos?per_page={per_page}&page={page}"
        data = github_api(url)
        if not data:
            break
        repos.extend(data)
        if len(data) < per_page:
            break
        page += 1
    return repos


def fetch_repo_languages(owner: str, repo: str):
    """
    1リポジトリの言語使用量を取得する。
    例: {"C#": 12345, "Python": 2345}
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/languages"
    try:
        return github_api(url)
    except Exception:
        # Privateで読めない・Archivedなどは空で返す
        return {}


def fetch_repo_contributors(owner: str, repo: str):
    """
    1リポジトリのcontributorsを取得する。
    例: [{"login": "foo", "contributions": 10}, ...]
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=100"
    try:
        return github_api(url)
    except Exception:
        return []


# ====== 言語集計 ======
def aggregate_languages(repos, owner: str):
    """
    全リポジトリの /languages を叩いて合計する
    戻り値: Counter({"C#": 12345, "TypeScript": 4567, ...})
    """
    lang_counter = Counter()
    for r in repos:
        name = r["name"]
        langs = fetch_repo_languages(owner, name)
        for lang, size in langs.items():
            lang_counter[lang] += size
    return lang_counter


def save_language_svg(lang_counter, path: str):
    """
    言語カウンタから円グラフSVGを生成して保存する。
    """
    if not lang_counter:
        # 何もなければ空の図を出す
        fig, ax = plt.subplots()
        ax.text(0.5, 0.5, "No language data", ha="center", va="center")
        fig.savefig(path, format="svg", bbox_inches="tight")
        return

    # 上位8くらいに絞る
    most_common = lang_counter.most_common(8)
    labels = [k for k, _ in most_common]
    sizes = [v for _, v in most_common]

    fig, ax = plt.subplots()
    ax.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.axis("equal")
    fig.savefig(path, format="svg", bbox_inches="tight")


# ====== Contributors 集計 ======
def aggregate_contributors(repos, owner: str, top_n: int = 10):
    """
    全リポジトリのcontributorsを合算して「誰が一番コミットしてるか」を出す。
    戻り値: list[(login, count)]
    """
    total = Counter()
    for r in repos:
        name = r["name"]
        contributors = fetch_repo_contributors(owner, name)
        for c in contributors:
            login = c.get("login")
            cnt = c.get("contributions", 0)
            if login:
                total[login] += cnt
    return total.most_common(top_n)


# ====== 衛星カテゴリ分け ======
def group_repos_by_satellite(repos):
    """
    リポジトリ名から衛星ごとに分類する。
    name にキーワードが含まれていたらそのグループに入れる。
    なければ OTHERS に入れる。
    """
    grouped = {k: [] for k in SATELLITE_GROUPS.keys()}
    grouped[OTHER_GROUP] = []

    for r in repos:
        name = r["name"]
        lower = name.lower()
        found = False
        for sat_name, keywords in SATELLITE_GROUPS.items():
            if any(kw.lower() in lower for kw in keywords):
                grouped[sat_name].append(r)
                found = True
                break
        if not found:
            grouped[OTHER_GROUP].append(r)
    return grouped


# ====== Markdown 生成 ======
def make_recent_repos_table(repos, limit=10):
    """
    最近更新されたリポジトリのテーブルを作る
    """
    lines = []
    lines.append("### 📦 最近動いたリポジトリ\n")
    lines.append("| Repo | Pushed | Stars | Lang |\n")
    lines.append("|------|--------|-------|------|\n")
    for r in repos[:limit]:
        name = r["name"]
        html_url = r["html_url"]
        pushed_at = r["pushed_at"] or "-"
        stars = r["stargazers_count"]
        lang = r["language"] or "-"
        lines.append(f"| [{name}]({html_url}) | {pushed_at[:10]} | ⭐ {stars} | {lang} |\n")
    lines.append("\n")
    return "".join(lines)


def make_contributors_section(top_contribs):
    """
    コントリビュータランキングの Markdown を作る
    """
    lines = []
    lines.append("### 🧑‍💻 Top Contributors (all repos)\n")
    if not top_contribs:
        lines.append("データがありません。\n\n")
        return "".join(lines)

    lines.append("| User | Contributions |\n")
    lines.append("|------|----------------|\n")
    for login, cnt in top_contribs:
        lines.append(f"| @{login} | {cnt} |\n")
    lines.append("\n")
    return "".join(lines)


def make_language_section(lang_counter):
    """
    言語サマリの Markdown を作る
    """
    lines = []
    lines.append("### 🗣️ Language Summary (org-wide)\n")
    if not lang_counter:
        lines.append("言語データが取得できませんでした。\n\n")
        return "".join(lines)

    total = sum(lang_counter.values())
    lines.append("| Language | Bytes | Ratio |\n")
    lines.append("|----------|-------|-------|\n")
    for lang, size in lang_counter.most_common(10):
        ratio = (size / total) * 100 if total else 0
        lines.append(f"| {lang} | {size} | {ratio:.1f}% |\n")
    lines.append("\n")
    lines.append("※ グラフ版は下の `assets/langs.svg` を参照\n\n")
    return "".join(lines)


def make_satellite_section(grouped):
    """
    衛星ごとのリポジトリ一覧を Markdown で出す
    """
    lines = []
    lines.append("### 🛰️ Satellite Projects\n")
    for sat, repos in grouped.items():
        if not repos:
            continue
        lines.append(f"#### {sat}\n")
        for r in sorted(repos, key=lambda x: x["name"].lower()):
            lines.append(f"- [{r['name']}]({r['html_url']})\n")
        lines.append("\n")
    return "".join(lines)


def main():
    if not ORG_NAME:
        print("ERROR: ORG_NAME is not set", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] Fetching repos for org: {ORG_NAME}")
    repos = fetch_all_repos(ORG_NAME)
    if not repos:
        print("[WARN] No repos found.")
        sys.exit(0)

    # 最近更新順にソート
    repos.sort(key=lambda r: r["pushed_at"] or "", reverse=True)

    # 直近30日でアクティブなリポジトリ数
    now = datetime.now(timezone.utc)
    active_30d = 0
    for r in repos:
        if not r["pushed_at"]:
            continue
        dt = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        if (now - dt).days <= 30:
            active_30d += 1

    # ① 言語サマリ
    print("[INFO] Aggregating languages...")
    lang_counter = aggregate_languages(repos, ORG_NAME)
    save_language_svg(lang_counter, LANG_SVG_PATH)
    print(f"[INFO] Saved language svg to {LANG_SVG_PATH}")

    # ② Contributors
    print("[INFO] Aggregating contributors...")
    top_contribs = aggregate_contributors(repos, ORG_NAME, top_n=10)

    # ③ 衛星カテゴリ分け
    grouped = group_repos_by_satel_
