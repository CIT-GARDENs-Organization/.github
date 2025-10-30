#!/usr/bin/env python3
# scripts/org_stats.py
"""
Orgプロフィール用のREADMEを自動更新するスクリプト。
- 組織の全リポジトリを取得
- 最近動いたリポジトリの表
- 言語サマリ
- コントリビュータランキング
- 衛星別リポジトリ一覧
を生成し、README内の <!-- ORG-STATS:START --> ... <!-- ORG-STATS:END --> を置き換える。
さらに言語サマリの円グラフSVGを保存する。
"""

import os
import sys
import json
import math
from datetime import datetime, timezone
from urllib import request
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ====== 設定（環境変数から取るように変更） ======
ORG_NAME = os.environ.get("ORG_NAME", "").strip()
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
README_PATH = os.environ.get("README_PATH", "README.md").strip()
LANG_SVG_PATH = os.environ.get("LANG_SVG_PATH", "assets/langs.svg").strip()

BLOCK_START = "<!-- ORG-STATS:START -->"
BLOCK_END = "<!-- ORG-STATS:END -->"

# 衛星名のパターン（必要ならここに増やす）
SATELLITE_GROUPS = {
    "YOMOGI": ["yomogi", "YOMOGI", "ymg", "YMG"],
    "KASHIWA": ["kashiwa", "KASHIWA", "ksh", "KSH"],
    "SAKURA": ["sakura", "SAKURA", "skr", "SKR"],
    "BOTAN": ["botan", "BOTAN", "btn", "BTN"],
    "MOMIJI": ["momiji", "MOMIJI", "mmj", "MMJ"]
}
OTHER_GROUP = "OTHERS"

with open("scripts/github_colors.json", "r", encoding="utf-8") as f:
    GITHUB_LANG_COLORS = json.load(f)

# ========== GitHub API helper ==========
def github_api(url: str):
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
    url = f"https://api.github.com/repos/{owner}/{repo}/languages"
    try:
        return github_api(url)
    except Exception:
        return {}


def fetch_repo_contributors(owner: str, repo: str):
    url = f"https://api.github.com/repos/{owner}/{repo}/contributors?per_page=100"
    try:
        return github_api(url)
    except Exception:
        return []


# ========== 言語集計 ==========
def aggregate_languages(repos, owner: str):
    counter = Counter()
    for r in repos:
        langs = fetch_repo_languages(owner, r["name"])
        for lang, size in langs.items():
            counter[lang] += size
    return counter


def save_language_svg(lang_counter, path: str):
    """
    言語カウンタから円グラフSVGを生成して保存する。
    - ラベルを外に出して線でつなぐ
    - GitHub公式に近い色を使う（ある分だけ）
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 4))

    if not lang_counter:
        ax.text(0.5, 0.5, "No language data", ha="center", va="center")
        fig.savefig(path, format="svg", bbox_inches="tight")
        return

    most_common = lang_counter.most_common(8)
    labels = [k for k, _ in most_common]
    sizes = [v for _, v in most_common]

    colors = [GITHUB_LANG_COLORS.get(lang, None) for lang in labels]

    # まず普通にパイを描く
    wedges, _ = ax.pie(
        sizes,
        colors=colors,
        startangle=90,
        radius=1.0
    )

    # ラベル＋線
    kw = dict(arrowprops=dict(arrowstyle="-", lw=0.8),
              va="center",
              fontsize=8)
    total = sum(sizes)
    for i, w in enumerate(wedges):
        ang = (w.theta2 + w.theta1) / 2.0
        # 円周上の点
        x = math.cos(math.radians(ang))
        y = math.sin(math.radians(ang))
        # 外に出す位置
        x_text = 1.25 * x
        y_text = 1.25 * y
        ha = "left" if x > 0 else "right"
        percent = sizes[i] / total * 100.0
        ax.annotate(
            f"{labels[i]} ({percent:.1f}%)",
            xy=(x, y),
            xytext=(x_text, y_text),
            ha=ha,
            **kw
        )

    ax.set_aspect("equal")

    # 凡例も右に出すと読みやすい
    ax.legend(
        wedges,
        labels,
        title="Languages",
        loc="center left",
        bbox_to_anchor=(1.05, 0.0, 0.4, 1.0)
    )

    fig.savefig(path, format="svg", bbox_inches="tight")


# ========== Contributors ==========
def aggregate_contributors(repos, owner: str, top_n: int = 10):
    total = Counter()
    for r in repos:
        contribs = fetch_repo_contributors(owner, r["name"])
        for c in contribs:
            login = c.get("login")
            cnt = c.get("contributions", 0)
            if login:
                total[login] += cnt
    return total.most_common(top_n)


# ========== 衛星ごと ==========
def group_repos_by_satellite(repos):
    grouped = {k: [] for k in SATELLITE_GROUPS.keys()}
    grouped[OTHER_GROUP] = []

    for r in repos:
        name = r["name"]
        lower = name.lower()
        put = False
        for sat, kws in SATELLITE_GROUPS.items():
            if any(kw.lower() in lower for kw in kws):
                grouped[sat].append(r)
                put = True
                break
        if not put:
            grouped[OTHER_GROUP].append(r)
    return grouped


# ========== Markdown builders ==========
def make_recent_repos_table(repos, limit=10):
    lines = []
    lines.append("### 📦 最近動いたリポジトリ\n")
    lines.append("| Repo | Pushed | Stars | Lang |\n")
    lines.append("|------|--------|-------|------|\n")
    for r in repos[:limit]:
        lines.append(
            f"| [{r['name']}]({r['html_url']}) | {(r['pushed_at'] or '-')[:10]} | ⭐ {r['stargazers_count']} | {r['language'] or '-'} |\n"
        )
    lines.append("\n")
    return "".join(lines)


def make_language_section(lang_counter):
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
    lines.append("※ グラフ版は `../assets/langs.svg` を参照\n\n")
    return "".join(lines)


def make_contributors_section(top_contribs):
    lines = []
    lines.append("### 🧑‍💻 Top Contributors (all repos)\n")
    if not top_contribs:
        lines.append("データがありませんでした。\n\n")
        return "".join(lines)
    lines.append("| User | Contributions |\n")
    lines.append("|------|----------------|\n")
    for login, cnt in top_contribs:
        lines.append(f"| @{login} | {cnt} |\n")
    lines.append("\n")
    return "".join(lines)


def make_satellite_section(grouped):
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

    print(f"[INFO] fetch repos for org={ORG_NAME}")
    repos = fetch_all_repos(ORG_NAME)
    if not repos:
        print("[WARN] no repos found")
        sys.exit(0)

    # 最近順
    repos.sort(key=lambda r: r["pushed_at"] or "", reverse=True)

    # 直近30日
    now = datetime.now(timezone.utc)
    active_30d = 0
    for r in repos:
        if not r["pushed_at"]:
            continue
        dt = datetime.fromisoformat(r["pushed_at"].replace("Z", "+00:00"))
        if (now - dt).days <= 30:
            active_30d += 1

    # 言語
    print("[INFO] aggregate languages")
    lang_counter = aggregate_languages(repos, ORG_NAME)
    save_language_svg(lang_counter, LANG_SVG_PATH)
    print(f"[INFO] saved svg -> {LANG_SVG_PATH}")

    # contributors
    print("[INFO] aggregate contributors")
    top_contribs = aggregate_contributors(repos, ORG_NAME, top_n=10)

    # 衛星
    grouped = group_repos_by_satellite(repos)

    now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    # Markdown部
    md = []
    md.append(f"最終更新: {now_iso}\n\n")
    md.append(f"- リポジトリ総数: **{len(repos)}**\n")
    md.append(f"- 直近30日で更新があったリポジトリ: **{active_30d}**\n\n")
    md.append(make_recent_repos_table(repos, limit=10))
    md.append(make_language_section(lang_counter))
    md.append(make_contributors_section(top_contribs))
    md.append(make_satellite_section(grouped))

    new_block = "".join(md)

    # READMEを差し替え
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    if BLOCK_START not in readme or BLOCK_END not in readme:
        print("ERROR: placeholders not found in", README_PATH, file=sys.stderr)
        sys.exit(1)

    before, _, tail = readme.partition(BLOCK_START)
    _, _, after = tail.partition(BLOCK_END)

    new_readme = before + BLOCK_START + "\n" + new_block + BLOCK_END + after

    if new_readme != readme:
        with open(README_PATH, "w", encoding="utf-8") as f:
            f.write(new_readme)
        print("[INFO] updated", README_PATH)
    else:
        print("[INFO] no change")


if __name__ == "__main__":
    main()
