#!/usr/bin/env python3
"""Collect daily research topics, save Markdown, and send one LINE message."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[1]
RESEARCH_FILE = Path(os.environ.get("RESEARCH_FILE", ROOT_DIR / "github_data" / "Research.md"))
GENERATED_DIR = Path(os.environ.get("GENERATED_DIR", ROOT_DIR / "github_data" / "Generated"))
LOG_DIR = Path(os.environ.get("LOG_DIR", ROOT_DIR / "github_data" / "logs"))
LINE_ENDPOINT = "https://api.line.me/v2/bot/message/push"
JST = ZoneInfo("Asia/Tokyo")


class MorningLineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Topic:
    title: str
    link: str
    published: str
    source: str


def now_jst() -> datetime:
    return datetime.now(JST)


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_research(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        raise MorningLineError(f"Research.mdが見つかりません: {path}")

    categories: Dict[str, List[str]] = {}
    current = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = clean_text(line[3:])
            if current:
                categories.setdefault(current, [])
            continue
        match = re.match(r"^[-*]\s+(.+)$", line)
        if current and match:
            keyword = clean_text(match.group(1))
            if keyword and not keyword.startswith("["):
                categories[current].append(keyword)

    categories = {name: items for name, items in categories.items() if items}
    if not categories:
        raise MorningLineError("Research.mdに『## カテゴリ』と『- キーワード』を入力してください")
    return categories


def google_news_rss_url(category: str, keywords: List[str]) -> str:
    terms = " OR ".join(f'"{word}"' for word in keywords)
    query = quote_plus(f"{category} ({terms}) when:7d")
    return f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_rss(url: str, limit: int = 5) -> List[Topic]:
    request = Request(url, headers={"User-Agent": "COSP-Morning-Research/1.0"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read()
    except HTTPError as exc:
        raise MorningLineError(f"RSS取得エラー HTTP {exc.code}") from exc
    except URLError as exc:
        raise MorningLineError(f"RSS接続エラー: {exc.reason}") from exc

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise MorningLineError("RSSのXMLを解析できませんでした") from exc

    topics: List[Topic] = []
    seen = set()
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title", ""))
        link = clean_text(item.findtext("link", ""))
        if not title or not link or (title, link) in seen:
            continue
        seen.add((title, link))
        topics.append(
            Topic(
                title=title,
                link=link,
                published=clean_text(item.findtext("pubDate", "")),
                source=clean_text(item.findtext("source", "")) or "Google News",
            )
        )
        if len(topics) >= limit:
            break
    return topics


def collect_topics(categories: Dict[str, List[str]]) -> Dict[str, List[Topic]]:
    collected: Dict[str, List[Topic]] = {}
    errors: List[str] = []
    for category, keywords in categories.items():
        try:
            topics = fetch_rss(google_news_rss_url(category, keywords))
        except MorningLineError as exc:
            errors.append(f"{category}: {exc}")
            continue
        if topics:
            collected[category] = topics
        else:
            errors.append(f"{category}: 該当記事なし")

    if not collected:
        detail = " / ".join(errors) if errors else "取得結果なし"
        raise MorningLineError(f"すべてのカテゴリでネタ収集に失敗しました: {detail}")
    return collected


def markdown_text(categories: Dict[str, List[str]], collected: Dict[str, List[Topic]]) -> str:
    now = now_jst()
    lines = [
        "---",
        "type: morning_research",
        f"date: {now:%Y-%m-%d}",
        f"created_at: {now:%Y-%m-%d %H:%M:%S %Z}",
        "source: Google News RSS",
        "---",
        "",
        f"# 朝のネタ収集 {now:%Y-%m-%d}",
        "",
        "Research.mdのカテゴリをもとに収集。投稿前にリンク先の内容を確認する。",
    ]
    for category, keywords in categories.items():
        lines.extend(["", f"## {category}", "", f"調査キーワード: {', '.join(keywords)}", ""])
        topics = collected.get(category, [])
        if not topics:
            lines.append("- 取得できませんでした")
            continue
        for topic in topics:
            meta = " / ".join(part for part in [topic.source, topic.published] if part)
            lines.append(f"- [{topic.title}]({topic.link})")
            if meta:
                lines.append(f"  - {meta}")
    lines.extend(["", "## 投稿前チェック", "", "- [ ] 元記事を開いて事実関係を確認した", "- [ ] 投稿日と情報の鮮度を確認した", "- [ ] 個人情報や未公開情報を含めていない", ""])
    return "\n".join(lines)


def success_line_message(collected: Dict[str, List[Topic]], output_path: Path) -> str:
    lines = ["【朝のAI秘書】", "", "今朝の投稿ネタを収集しました。"]
    for category, topics in collected.items():
        lines.extend(["", f"■ {category}"])
        for topic in topics[:2]:
            lines.append(f"・{topic.title}")
    lines.extend(["", f"保存先: {output_path.relative_to(ROOT_DIR)}", "", "投稿前に元記事を確認してください。"])
    return "\n".join(lines)[:5000]


def workflow_run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repository and run_id:
        return f"{server}/{repository}/actions/runs/{run_id}"
    return "GitHub Actionsの実行履歴を確認してください"


def failure_line_message(reason: str) -> str:
    return "\n".join(
        [
            "【朝のAI秘書・処理失敗】",
            "",
            f"発生日時: {now_jst():%Y-%m-%d %H:%M:%S JST}",
            f"内容: {clean_text(reason)[:500]}",
            "",
            f"確認先: {workflow_run_url()}",
        ]
    )


def send_line(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_TO_USER_ID", "").strip()
    if not token:
        raise MorningLineError("LINE_CHANNEL_ACCESS_TOKENが未設定です")
    if not user_id:
        raise MorningLineError("LINE_TO_USER_IDが未設定です")

    payload = {"to": user_id, "messages": [{"type": "text", "text": text[:5000]}]}
    request = Request(
        LINE_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise MorningLineError(f"LINE送信エラー HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise MorningLineError(f"LINE接続エラー: {exc.reason}") from exc


def write_log(status: str, detail: str, output_path: Path | None = None) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = now_jst()
    suffix = "success" if status == "success" else "failure"
    path = LOG_DIR / f"{now:%Y-%m-%d_%H%M%S}_{suffix}.md"
    lines = [
        f"# Morning Research {status}",
        "",
        f"- 実行日時: {now:%Y-%m-%d %H:%M:%S JST}",
        f"- ステータス: {status}",
        f"- 詳細: {clean_text(detail)}",
    ]
    if output_path:
        lines.append(f"- 生成ファイル: {output_path.relative_to(ROOT_DIR)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run(dry_run: bool = False) -> Path:
    categories = parse_research(RESEARCH_FILE)
    collected = collect_topics(categories)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / f"{now_jst():%Y-%m-%d}_朝のネタ収集.md"
    output_path.write_text(markdown_text(categories, collected), encoding="utf-8")
    message = success_line_message(collected, output_path)
    if dry_run:
        print(message)
    else:
        send_line(message)
    write_log("success", f"{len(collected)}カテゴリを収集", output_path)
    print(output_path.relative_to(ROOT_DIR))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="LINEへ送らずに収集・保存する")
    parser.add_argument("--notify-failure", metavar="REASON", help="workflow失敗通知だけを送る")
    args = parser.parse_args()

    if args.notify_failure:
        try:
            send_line(failure_line_message(args.notify_failure))
            print("LINE failure notification sent")
            return 0
        except Exception as exc:
            print(f"Failure notification error: {exc}", file=sys.stderr)
            return 1

    try:
        run(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        write_log("failure", str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
