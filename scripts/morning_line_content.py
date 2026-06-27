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
AFFILIATE_FILE = Path(os.environ.get("AFFILIATE_FILE", ROOT_DIR / "github_data" / "Affiliate_Links.md"))
GENERATED_DIR = Path(os.environ.get("GENERATED_DIR", ROOT_DIR / "github_data" / "Generated"))
LOG_DIR = Path(os.environ.get("LOG_DIR", ROOT_DIR / "github_data" / "logs"))
LINE_ENDPOINT = "https://api.line.me/v2/bot/message/push"
LINE_BOT_INFO_ENDPOINT = "https://api.line.me/v2/bot/info"
JST = ZoneInfo("Asia/Tokyo")


class MorningLineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Topic:
    title: str
    link: str
    published: str
    source: str


@dataclass(frozen=True)
class AffiliateLink:
    category: str
    product: str
    url: str
    keywords: tuple[str, ...]
    guide: str


@dataclass(frozen=True)
class PostCandidate:
    category: str
    title: str
    platforms: str
    body: str
    hashtags: str
    affiliate_guide: str
    planned_link: str
    audience: str
    intent: str
    source_title: str
    source_link: str


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


def parse_affiliate_links(path: Path) -> Dict[str, List[AffiliateLink]]:
    if not path.exists():
        raise MorningLineError(f"Affiliate_Links.mdが見つかりません: {path}")

    links: Dict[str, List[AffiliateLink]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [clean_text(cell) for cell in line.strip("|").split("|")]
        if len(cells) != 5 or cells[0] in {"カテゴリ", "---"}:
            continue
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        category, product, url, keywords_text, guide = cells
        keywords = tuple(clean_text(word) for word in keywords_text.split(",") if clean_text(word))
        if category and product and url.startswith("https://") and keywords and guide:
            links.setdefault(category, []).append(AffiliateLink(category, product, url, keywords, guide))

    if not links:
        raise MorningLineError("Affiliate_Links.mdに有効なリンクがありません")
    return links


def google_news_rss_url(
    category: str, keywords: List[str], focus_keywords: tuple[str, ...] = ()
) -> str:
    terms = " OR ".join(f'"{word}"' for word in keywords)
    focus = " OR ".join(f'"{word}"' for word in focus_keywords)
    focus_query = f" ({focus})" if focus else ""
    query = quote_plus(f"{category} ({terms}){focus_query} when:14d")
    return f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"


def fetch_rss(url: str, limit: int = 10) -> List[Topic]:
    request = Request(url, headers={"User-Agent": "COSP-Morning-Research/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
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


def collect_topics(
    categories: Dict[str, List[str]], affiliates: Dict[str, List[AffiliateLink]]
) -> Dict[str, List[Topic]]:
    collected: Dict[str, List[Topic]] = {}
    errors: List[str] = []
    for category, category_affiliates in affiliates.items():
        research_keywords = categories.get(category)
        if not research_keywords:
            errors.append(f"{category}: Research.mdに同名カテゴリなし")
            continue
        seen = {(topic.title, topic.link) for topic in collected.get(category, [])}
        for affiliate in category_affiliates:
            try:
                topics = fetch_rss(
                    google_news_rss_url(category, research_keywords, affiliate.keywords)
                )
            except MorningLineError as exc:
                errors.append(f"{category}/{affiliate.product}: {exc}")
                continue
            for topic in topics:
                key = (topic.title, topic.link)
                if key not in seen:
                    collected.setdefault(category, []).append(topic)
                    seen.add(key)
            if not topics:
                errors.append(f"{category}/{affiliate.product}: 該当記事なし")

    if not collected:
        detail = " / ".join(errors) if errors else "取得結果なし"
        raise MorningLineError(f"すべてのカテゴリでネタ収集に失敗しました: {detail}")
    return collected


def category_profile(category: str) -> Dict[str, str]:
    if "家づくり" in category:
        return {
            "platforms": "Threads / X",
            "audience": "これから家づくりを始める人、設備や間取りを比較中の人",
            "intent": "ニュースを自分の家づくりへ置き換える視点を共有する",
            "insight": "家づくり中は、決まったことだけでなく、迷った理由や比較した条件を残す方があとで役立つと感じています。",
            "hashtags": "#家づくり #平屋 #後悔しない家づくり #PR",
        }
    if "AI" in category:
        return {
            "platforms": "X / note",
            "audience": "ChatGPTやCodexを仕事や発信に取り入れたい人",
            "intent": "AIニュースを、毎日の作業改善という現実的な視点で伝える",
            "insight": "AIは新機能を追うだけより、入力・確認・保存の流れを固定した方が日常で使い続けやすいです。",
            "hashtags": "#AI活用 #ChatGPT #Codex #自動化 #PR",
        }
    if "ガジェット" in category:
        return {
            "platforms": "Threads / Instagram",
            "audience": "撮影機材やデスク環境を改善したい人",
            "intent": "話題の商品を、スペックではなく使う場面から考えるきっかけにする",
            "insight": "ガジェットは性能だけで選ぶと持て余すことがあります。先に使う場面を決めると、必要な機能が見えやすくなります。",
            "hashtags": "#ガジェット #デスク環境 #動画制作 #Amazon #PR",
        }
    return {
        "platforms": "Threads / note",
        "audience": "暮らしを少し楽にしたい人",
        "intent": "ニュースを日々の工夫へ置き換えて共有する",
        "insight": "便利そうという印象だけで決めず、自分の生活で続けられるかを考えることが大切です。",
        "hashtags": "#暮らし #家事効率化 #PR",
    }


def short_headline(title: str, limit: int = 84) -> str:
    headline = title.rsplit(" - ", 1)[0].strip()
    return headline if len(headline) <= limit else headline[: limit - 1].rstrip() + "…"


def build_candidates(
    collected: Dict[str, List[Topic]], affiliates: Dict[str, List[AffiliateLink]], limit: int = 5
) -> List[PostCandidate]:
    candidates: List[PostCandidate] = []
    for category, category_affiliates in affiliates.items():
        topics = collected.get(category, [])
        if not topics:
            continue
        profile = category_profile(category)
        unused_topics = list(topics)
        for affiliate in category_affiliates:
            if not unused_topics:
                break
            topic = max(
                unused_topics,
                key=lambda item: sum(
                    1 for keyword in affiliate.keywords if keyword.lower() in item.title.lower()
                ),
            )
            score = sum(
                1 for keyword in affiliate.keywords if keyword.lower() in topic.title.lower()
            )
            if score == 0:
                continue
            unused_topics.remove(topic)
            headline = short_headline(topic.title)
            post_title = short_headline(topic.title, 54) + "を見て考えたこと"
            affiliate_guide = affiliate.guide + " 必要な方だけ確認してください。"
            body = "\n".join(
                [
                    f"「{headline}」という話題を見かけました。",
                    "",
                    profile["insight"],
                    "",
                    affiliate_guide,
                    "※アフィリエイトリンクを含みます。",
                    affiliate.url,
                ]
            )
            candidates.append(
                PostCandidate(
                    category=category,
                    title=post_title,
                    platforms=profile["platforms"],
                    body=body,
                    hashtags=profile["hashtags"],
                    affiliate_guide=affiliate_guide,
                    planned_link=affiliate.url,
                    audience=profile["audience"],
                    intent=profile["intent"],
                    source_title=topic.title,
                    source_link=topic.link,
                )
            )
            if len(candidates) >= limit:
                return candidates

    if len(candidates) < 3:
        raise MorningLineError(
            "投稿候補が3件未満です。Research.mdとAffiliate_Links.mdのカテゴリ名を合わせてください"
        )
    return candidates


def candidate_lines(candidate: PostCandidate, number: int) -> List[str]:
    return [
        f"## 候補{number}: {candidate.category}",
        "",
        f"- 投稿タイトル: {candidate.title}",
        f"- 投稿先おすすめ: {candidate.platforms}",
        f"- 想定読者: {candidate.audience}",
        f"- 投稿意図: {candidate.intent}",
        f"- アフィリエイト導線: {candidate.affiliate_guide}",
        f"- 使用予定リンク: {candidate.planned_link}",
        f"- 参考ネタ: [{candidate.source_title}]({candidate.source_link})",
        "",
        "### 本文",
        "",
        candidate.body,
        "",
        "### ハッシュタグ",
        "",
        candidate.hashtags,
    ]


def markdown_text(candidates: List[PostCandidate]) -> str:
    now = now_jst()
    lines = [
        "---",
        "type: morning_affiliate_post_candidates",
        f"date: {now:%Y-%m-%d}",
        f"created_at: {now:%Y-%m-%d %H:%M:%S %Z}",
        "source: Google News RSS",
        "auto_post: false",
        "---",
        "",
        f"# 朝のアフィリエイト投稿候補 {now:%Y-%m-%d}",
        "",
        "Research.mdのカテゴリをもとに作成。自動投稿せず、人間が確認してから使用する。",
    ]
    for number, candidate in enumerate(candidates, 1):
        lines.extend([""] + candidate_lines(candidate, number))
    lines.extend(
        [
            "",
            "## 投稿前チェック",
            "",
            "- [ ] 元記事を開いて事実関係を確認した",
            "- [ ] 商品との関連が不自然ではない",
            "- [ ] アフィリエイト表示とリンクを確認した",
            "- [ ] 実際に確認していない体験を断定していない",
            "- [ ] 自動投稿ではなく人間が確認した",
            "",
        ]
    )
    return "\n".join(lines)


def success_line_message(candidates: List[PostCandidate], output_path: Path) -> str:
    lines = ["【朝のResearch AI】", "", f"本日の投稿候補は{len(candidates)}件です。"]
    for number, candidate in enumerate(candidates, 1):
        lines.extend(
            [
                "",
                f"【候補{number}｜{candidate.category}】",
                f"投稿タイトル: {candidate.title}",
                f"投稿先おすすめ: {candidate.platforms}",
                "",
                "本文:",
                candidate.body,
                "",
                f"ハッシュタグ: {candidate.hashtags}",
                f"アフィリエイト導線: {candidate.affiliate_guide}",
                f"使用予定リンク: {candidate.planned_link}",
                f"想定読者: {candidate.audience}",
                f"投稿意図: {candidate.intent}",
            ]
        )
    lines.extend(["", f"保存先: {output_path.relative_to(ROOT_DIR)}", "", "内容を確認してから手動投稿してください。"])
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


def line_credentials() -> tuple[str, str]:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_TO_USER_ID", "").strip()
    if not token:
        raise MorningLineError("LINE_CHANNEL_ACCESS_TOKENが未設定です")
    if not user_id:
        raise MorningLineError("LINE_TO_USER_IDが未設定です")
    if len(token) < 100:
        raise MorningLineError("LINE_CHANNEL_ACCESS_TOKENが短すぎます。GitHub Secretを再登録してください")
    if not re.fullmatch(r"U[0-9a-fA-F]{32}", user_id):
        raise MorningLineError("LINE_TO_USER_IDの形式が正しくありません")
    return token, user_id


def check_line_connection() -> None:
    token, _ = line_credentials()
    request = Request(LINE_BOT_INFO_ENDPOINT, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(request, timeout=30) as response:
            response.read()
    except HTTPError as exc:
        if exc.code == 401:
            raise MorningLineError(
                "GitHub Secret LINE_CHANNEL_ACCESS_TOKENが無効または期限切れです"
            ) from exc
        detail = exc.read().decode("utf-8", errors="replace")
        raise MorningLineError(f"LINE接続確認エラー HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise MorningLineError(f"LINE接続確認エラー: {exc.reason}") from exc


def send_line(text: str) -> None:
    token, user_id = line_credentials()

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
        if exc.code == 401:
            raise MorningLineError(
                "GitHub Secret LINE_CHANNEL_ACCESS_TOKENが無効または期限切れです"
            ) from exc
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
    affiliates = parse_affiliate_links(AFFILIATE_FILE)
    collected = collect_topics(categories, affiliates)
    candidates = build_candidates(collected, affiliates)
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = GENERATED_DIR / f"{now_jst():%Y-%m-%d}_アフィリエイト投稿候補.md"
    output_path.write_text(markdown_text(candidates), encoding="utf-8")
    message = success_line_message(candidates, output_path)
    if dry_run:
        print(message)
    else:
        send_line(message)
    write_log("success", f"投稿候補を{len(candidates)}件生成", output_path)
    print(output_path.relative_to(ROOT_DIR))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="LINEへ送らずに収集・保存する")
    parser.add_argument("--check-line", action="store_true", help="LINE SecretsとAPI接続だけを確認する")
    parser.add_argument("--notify-failure", metavar="REASON", help="workflow失敗通知だけを送る")
    args = parser.parse_args()

    if args.check_line:
        try:
            check_line_connection()
            print("LINE credentials OK")
            return 0
        except Exception as exc:
            print(f"LINE credentials error: {exc}", file=sys.stderr)
            return 1

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
