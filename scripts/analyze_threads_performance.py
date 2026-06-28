#!/usr/bin/env python3
"""Analyze pending Threads result Markdown files and notify LINE."""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Dict, List

from morning_line_content import (
    check_line_connection,
    failure_line_message,
    now_jst,
    send_line_once,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
THREADS_DIR = ROOT_DIR / "github_data" / "SNS" / "Threads"
RESULTS_DIR = Path(
    os.environ.get("THREADS_RESULTS_DIR", THREADS_DIR / "Results")
)
ANALYSIS_DIR = Path(
    os.environ.get("THREADS_ANALYSIS_DIR", THREADS_DIR / "Analysis")
)


class PerformanceError(RuntimeError):
    pass


@dataclass
class PostResult:
    path: Path
    posted_at: datetime
    url: str
    category: str
    body: str
    views: int
    likes: int
    replies: int
    reposts: int
    saves: int
    hashtags: str
    affiliate: bool
    used_link: str
    screenshot: str
    memo: str

    @property
    def reactions(self) -> int:
        return self.likes + self.replies + self.reposts + self.saves

    @property
    def reaction_rate(self) -> float:
        return (self.reactions / self.views * 100) if self.views else 0.0

    @property
    def body_length(self) -> int:
        return len(self.body)

    @property
    def hashtag_count(self) -> int:
        return len(set(re.findall(r"#[^\s#]+", self.hashtags)))


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def frontmatter(text: str) -> Dict[str, str]:
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        raise PerformanceError("YAMLフロントマターがありません")
    values: Dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def section(text: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1).strip() if match else ""


def as_int(value: str, field: str, path: Path) -> int:
    try:
        number = int(str(value).replace(",", "").strip() or "0")
    except ValueError as exc:
        raise PerformanceError(f"{path.name}: {field}は整数で入力してください") from exc
    if number < 0:
        raise PerformanceError(f"{path.name}: {field}は0以上で入力してください")
    return number


def as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1", "あり", "有"}


def parse_posted_at(value: str, path: Path) -> datetime:
    normalized = value.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise PerformanceError(f"{path.name}: 投稿日時はYYYY-MM-DD HH:MMで入力してください")


def load_result(path: Path) -> PostResult:
    text = path.read_text(encoding="utf-8")
    meta = frontmatter(text)
    if meta.get("status", "ready").strip().lower() != "ready":
        raise PerformanceError("not_ready")
    if as_bool(meta.get("分析済み", "false")):
        raise PerformanceError("already_analyzed")
    body = section(text, "投稿本文")
    if not clean(body):
        raise PerformanceError(f"{path.name}: 投稿本文が空です")
    return PostResult(
        path=path,
        posted_at=parse_posted_at(meta.get("投稿日時", ""), path),
        url=meta.get("投稿URL", "") or "未入力",
        category=meta.get("投稿カテゴリ", "") or "未分類",
        body=body,
        views=as_int(meta.get("表示回数", "0"), "表示回数", path),
        likes=as_int(meta.get("いいね数", "0"), "いいね数", path),
        replies=as_int(meta.get("返信数", "0"), "返信数", path),
        reposts=as_int(meta.get("リポスト数", "0"), "リポスト数", path),
        saves=as_int(meta.get("保存数", "0"), "保存数", path),
        hashtags=section(text, "ハッシュタグ"),
        affiliate=as_bool(meta.get("アフィリエイトリンク有無", "false")),
        used_link=meta.get("使用リンク", "") or "なし",
        screenshot=meta.get("スクリーンショットファイル名", "") or "未入力",
        memo=section(text, "自己メモ"),
    )


def pending_results() -> List[PostResult]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results: List[PostResult] = []
    errors: List[str] = []
    for path in sorted(RESULTS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            results.append(load_result(path))
        except PerformanceError as exc:
            if str(exc) not in {"already_analyzed", "not_ready"}:
                errors.append(str(exc))
    if errors:
        raise PerformanceError(" / ".join(errors))
    return results


def good_points(post: PostResult, average_views: float, average_rate: float) -> List[str]:
    points: List[str] = []
    if post.views >= average_views and post.views > 0:
        points.append("表示回数が今回の平均以上")
    if post.reaction_rate >= average_rate and post.reactions > 0:
        points.append("反応率が今回の平均以上")
    if post.saves > 0:
        points.append("保存が発生し、見返す価値が伝わった")
    if post.replies > 0:
        points.append("返信が発生し、会話につながった")
    if post.reposts > 0:
        points.append("リポストされ、共有したい内容になった")
    if 250 <= post.body_length <= 700:
        points.append("Threadsで読みやすい文字量")
    return points or ["実績を記録できたため、次回比較の基準ができた"]


def improvement_points(post: PostResult, average_views: float, average_rate: float) -> List[str]:
    points: List[str] = []
    if post.views < average_views:
        points.append("冒頭1〜2行を短くし、悩みや結論を先に置く")
    if post.reaction_rate < average_rate or post.reactions == 0:
        points.append("最後に質問を置き、返信しやすい形を試す")
    if post.body_length < 250:
        points.append("体験→迷い→学びの順で背景を少し足す")
    elif post.body_length > 700:
        points.append("論点を1つに絞り、700文字以内へ圧縮する")
    if post.hashtag_count < 2:
        points.append("関連ハッシュタグを2〜4個へ増やす")
    elif post.hashtag_count > 6:
        points.append("ハッシュタグを4〜6個へ絞る")
    if post.posted_at.hour not in {7, 8, 9, 12, 13, 19, 20, 21, 22}:
        points.append("朝、昼休み、19〜22時の時間帯を比較する")
    if post.affiliate and post.reaction_rate < max(average_rate, 1.0):
        points.append("リンクを末尾へ移し、本文の価値提供を先にする")
    return points or ["同じ切り口を時間帯だけ変えて再検証する"]


def next_angle(best: PostResult | None) -> str:
    if not best:
        return "実績ファイルを追加し、共感型と学び型を比較する"
    if best.saves >= max(best.replies, best.reposts, 1):
        return "保存用チェックリスト型。3〜5項目に整理して見返せる投稿にする"
    if best.replies >= max(best.saves, best.reposts, 1):
        return "問いかけ型。迷っている選択肢を最後に質問して会話を作る"
    if best.reposts > 0:
        return "学びの要約型。失敗回避ポイントを短く共有する"
    if best.affiliate:
        return "比較型。商品紹介より先に、選ぶ基準と向かない人を書く"
    return "共感型。結論より先に、迷った場面と気付きを書く"


def next_post_draft(post: PostResult | None) -> str:
    category = post.category if post else "最近のテーマ"
    return "\n".join(
        [
            f"{category}で、まだ迷っていることがあります。",
            "",
            "最初はすぐ決めた方が楽だと思っていました。",
            "でも比較してみると、価格より『毎日どこで困るか』を先に決める方が大事でした。",
            "",
            "今は、良かった点だけでなく、見送った理由も残しています。",
            "同じことで迷っている方は、何を基準に選んでいますか？",
        ]
    )


def analyze(posts: List[PostResult]) -> Dict[str, object]:
    if not posts:
        return {
            "count": 0,
            "average_views": 0.0,
            "average_rate": 0.0,
            "best": None,
            "needs": None,
            "details": [],
            "angle": next_angle(None),
            "draft": next_post_draft(None),
        }
    average_views = mean(post.views for post in posts)
    average_rate = mean(post.reaction_rate for post in posts)
    best = max(posts, key=lambda post: (post.reaction_rate, post.views, post.saves))
    needs = min(posts, key=lambda post: (post.reaction_rate, post.views))
    details = [
        {
            "post": post,
            "good": good_points(post, average_views, average_rate),
            "improve": improvement_points(post, average_views, average_rate),
        }
        for post in posts
    ]
    return {
        "count": len(posts),
        "average_views": average_views,
        "average_rate": average_rate,
        "best": best,
        "needs": needs,
        "details": details,
        "angle": next_angle(best),
        "draft": next_post_draft(best),
    }


def post_label(post: PostResult | None) -> str:
    if not post:
        return "該当なし"
    return f"{post.path.stem}（表示{post.views}、反応率{post.reaction_rate:.2f}%）"


def analysis_markdown(data: Dict[str, object]) -> str:
    now = now_jst()
    lines = [
        "---",
        "type: threads_performance_analysis",
        f"date: {now:%Y-%m-%d}",
        f"created_at: {now:%Y-%m-%d %H:%M:%S %Z}",
        f"analyzed_posts: {data['count']}",
        "---",
        "",
        f"# Threads Performance Analysis {now:%Y-%m-%d}",
        "",
        f"- 今日分析した投稿数: {data['count']}",
        f"- 平均表示回数: {data['average_views']:.1f}",
        f"- 平均反応率: {data['average_rate']:.2f}%",
        f"- 伸びた投稿: {post_label(data['best'])}",
        f"- 改善が必要な投稿: {post_label(data['needs'])}",
    ]
    for index, detail in enumerate(data["details"], 1):
        post: PostResult = detail["post"]
        lines.extend(
            [
                "",
                f"## 投稿{index}: {post.path.stem}",
                "",
                f"- 投稿日時: {post.posted_at:%Y-%m-%d %H:%M}",
                f"- 投稿URL: {post.url}",
                f"- 投稿カテゴリ: {post.category}",
                f"- 表示回数: {post.views}",
                f"- 反応率: {post.reaction_rate:.2f}%",
                f"- 文字数: {post.body_length}",
                f"- ハッシュタグ数: {post.hashtag_count}",
                f"- 投稿時間: {post.posted_at.hour}時台",
                f"- アフィリエイト導線: {'あり' if post.affiliate else 'なし'}",
                f"- 使用リンク: {post.used_link}",
                f"- スクリーンショット: {post.screenshot}",
                "",
                "### 良かった点",
                "",
                *[f"- {point}" for point in detail["good"]],
                "",
                "### 改善点",
                "",
                *[f"- {point}" for point in detail["improve"]],
            ]
        )
    lines.extend(
        [
            "",
            "## 次回の投稿改善案",
            "",
            str(data["draft"]),
            "",
            "## 次に試すべき切り口",
            "",
            str(data["angle"]),
            "",
        ]
    )
    return "\n".join(lines)


def line_message(data: Dict[str, object], output_path: Path) -> str:
    best: PostResult | None = data["best"]
    needs: PostResult | None = data["needs"]
    lines = [
        "【Threads Performance AI】",
        "",
        f"今日分析した投稿数: {data['count']}",
        "",
        "【伸びた投稿】",
        post_label(best),
        "",
        "【改善が必要な投稿】",
        post_label(needs),
        "",
        "【次回の投稿改善案】",
        str(data["draft"]),
        "",
        "【次に試すべき切り口】",
        str(data["angle"]),
        "",
        f"保存先: {output_path.relative_to(ROOT_DIR)}",
    ]
    message = "\n".join(lines)
    if len(message) > 5000:
        raise PerformanceError(f"LINE本文が5000文字を超えています: {len(message)}")
    return message


def mark_analyzed(posts: List[PostResult]) -> None:
    date_text = now_jst().strftime("%Y-%m-%d")
    for post in posts:
        text = post.path.read_text(encoding="utf-8")
        text = re.sub(r"^分析済み:.*$", "分析済み: true", text, count=1, flags=re.MULTILINE)
        text = re.sub(r"^分析日:.*$", f"分析日: {date_text}", text, count=1, flags=re.MULTILINE)
        post.path.write_text(text, encoding="utf-8")


def run(dry_run: bool = False) -> Path:
    posts = pending_results()
    data = analyze(posts)
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = ANALYSIS_DIR / f"{now_jst():%Y-%m-%d}_threads_analysis.md"
    # Keep a useful analysis from an earlier manual run when the schedule finds no new posts.
    if posts or not output_path.exists():
        output_path.write_text(analysis_markdown(data), encoding="utf-8")
    message = line_message(data, output_path)
    if dry_run:
        print(message)
    else:
        send_line_once(message, "threads-performance")
        mark_analyzed(posts)
    print(output_path.relative_to(ROOT_DIR))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="LINE送信と分析済み更新を行わない")
    parser.add_argument("--check-line", action="store_true", help="LINE SecretsとAPI接続を確認する")
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
            reason = "Threads Performance AI: " + args.notify_failure
            send_line_once(
                failure_line_message(reason),
                "threads-performance-failure",
                fingerprint=reason,
            )
            print("LINE failure notification sent")
            return 0
        except Exception as exc:
            print(f"Failure notification error: {exc}", file=sys.stderr)
            return 1
    try:
        run(dry_run=args.dry_run)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
