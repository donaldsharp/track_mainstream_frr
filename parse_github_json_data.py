#!/usr/bin/env python3
"""
Parse Slack GitHub channel export files and summarize outside commentary.

This script reads daily JSON exports from a directory where each file is named
YYYY-MM-DD.json. It extracts:

1. Issues and pull requests with the unique set of GitHub users who commented
   or reviewed them.
2. A per-user summary showing how many distinct issues and pull requests each
   user commented on, how many they created, and how many PRs they merged.

Self-comments are excluded when the issue/PR creator appears in the dataset.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REPO_ITEM_LINK_RE = re.compile(
    r"<https?://github\.com/[^/]+/[^/]+/(?P<kind>pull|issues)/(?P<number>\d+)"
    r"(?:#[^|>]+)?\|#\d+[: ]\s*(?P<title>[^>]+)>"
)

CREATION_RE = re.compile(
    r"(?P<kind>Issue created|Pull request submitted) by "
    r"<https?://github\.com(?:/apps)?/[^|>]+\|(?P<owner>[^>]+)>"
)

INLINE_ACTIVITY_RE = re.compile(
    r"<https?://github\.com(?:/apps)?/[^|>]+\|(?P<actor>[^>]+)>\s+"
    r"(?P<action>commented on|requested changes to|approved|reviewed)\s+"
    r"<https?://github\.com/[^/]+/[^/]+/(?P<kind>pull|issues)/(?P<number>\d+)"
    r"(?:#[^|>]+)?\|#\d+:\s*(?P<title>[^>]+)>",
    re.IGNORECASE,
)

NEW_COMMENT_RE = re.compile(
    r"New comment by (?P<actor>.+?) on "
    r"(?P<label>pull request|issue) "
    r"<https?://github\.com/[^/]+/[^/]+/(?P<kind>pull|issues)/(?P<number>\d+)"
    r"(?:#[^|>]+)?\|#\d+:\s*(?P<title>[^>]+)>",
    re.IGNORECASE,
)

CLOSED_PR_RE = re.compile(
    r"Pull request closed:\s+"
    r"<https?://github\.com/[^/]+/[^/]+/pull/(?P<number>\d+)"
    r"(?:#[^|>]+)?\|#\d+[: ]\s*(?P<title>[^>]+)>\s+by\s+"
    r"<https?://github\.com(?:/apps)?/[^|>]+\|(?P<actor>[^>]+)>",
    re.IGNORECASE,
)

MERGE_COMMIT_RE = re.compile(
    r"Merge pull request #(?P<number>\d+)\b.*? - (?P<merger_name>[^\n`<]+)",
    re.IGNORECASE,
)

UNMAPPED_COMPANY = "Unknown / unmapped"

# Extend this over time as contributor affiliations become known.
USER_COMPANY_MAP = {
    "donaldsharp": "NVIDIA",
    "eqvinox": "Open Source Routing",
    "ton31337": "Open Source Routing",
    "mjstapp": "CISCO",
    "riw777": "NOKIA",
    "Jafaral": "ATCorp",
    "choppsv1": "LABN",
    "pguibert6WIND": "6WIND",
    "raja-rajasekar": "NVIDIA",
    "cscarpitta": "CISCO",
    "mwinter-osr": "Open Source Routing",
    "soumyar-roy": "NVIDIA",
    "y-bharath14": "SAMSUNG",
    "chiragshah6": "NVIDIA",
    "vjardin": "FREE",
    "anlancs": "TOM.COM",
    "RodrigoMNardi": "Open Source Routing",
    "louis-6wind": "6WIND",
    "enkechen-panw": "PALO ALTO NETWORKS",
    "zmw12306": "Graduate Student",
    "ashred-lnx": "NVIDIA",
    "hnattamaisub": "NVIDIA",
    "nabahr": "ATCorp",
    "hedrok": "VYOS",
    "Shbinging": "Graduate Student",
    "kaffarell": "PROXMOX",
    "lsang6WIND": "6WIND",
    "rzalamena": "Open Source Routing",
    "Pdoijode": "NVIDIA",
    "krishna-samy": "NVIDIA",
    "gromit1811": "UNKNOWN",
    "maxime-leroy": "FREE",
    "Manpreet-k0": "NVIDIA",
    "routingrocks": "NVIDIA",
    "jaredmauch": "AKAMAI",
    "sougatahitcs": "NVIDIA",
    "nanfengnan1": "GENEW",
    "Ko496-glitch": "Graduate Student",
    "fdumontet6WIND": "6WIND",
    "pbrisset": "CISCO",
    "taspelund": "OXIDE",
    "dmytroshytyi-6WIND": "6WIND",
    "louberger": "LABN",
    "miteshkanjariya": "NVIDIA",
    "enissim": "NVIDIA",
    "aceelindem": "LABN",
    "vijayalaxmi-basavaraj": "NVIDIA",
    "chdxD1": "TELEKOM",
    "davidschw": "OPEN SOURCE ROUTING",
    "ne-vlezay80": "YANDEX",
    "sever-sever": "VYOS",
    "aprathik04": "NVIDIA",
    "crosser": "IONOS Cloud",
    "drosarius": "ZSCALAR",
    "ak503": "BK.RU",
    "robinchrist": "rchrist.io",
    "rjarry": "REDHAT",
}


@dataclass(frozen=True, order=True)
class ItemKey:
    kind: str
    number: int


@dataclass
class ItemRecord:
    key: ItemKey
    title: str
    owner: str | None = None
    commenters: set[str] = field(default_factory=set)
    closed_by: str | None = None
    merge_commit_author: str | None = None
    merged_by: str | None = None

    @property
    def display_type(self) -> str:
        return "PR" if self.key.kind == "pull" else "Issue"

    @property
    def display_name(self) -> str:
        return f"#{self.key.number} {self.title}".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Parse Slack GitHub export JSON files and summarize outside "
            "issue/PR commentary."
        )
    )
    parser.add_argument(
        "directory",
        help="Directory containing daily Slack export JSON files",
    )
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="Include bot accounts in per-item and per-user output",
    )
    parser.add_argument(
        "--skip-unknown-owner-items",
        action="store_true",
        help=(
            "Skip items whose creator was not found in the supplied dataset. "
            "Useful when you want to avoid possible self-comments on older "
            "issues/PRs."
        ),
    )
    return parser.parse_args()


def is_bot(username: str) -> bool:
    lowered = username.lower()
    return lowered.endswith("[bot]") or lowered == "github-actions"


def company_for_user(username: str) -> str:
    return USER_COMPANY_MAP.get(username, UNMAPPED_COMPANY)


def format_created_pr_ratio(
    issue_count: int,
    pr_count: int,
    created_issue_count: int,
    created_pr_count: int,
    merged_pr_count: int,
) -> str:
    if created_pr_count == 0:
        return "N/A"

    numerator = issue_count + pr_count + created_issue_count + merged_pr_count
    ratio = numerator / created_pr_count
    return f"{ratio:.2f}"


def iter_export_files(directory: Path) -> Iterable[Path]:
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def load_messages(path: Path) -> list[dict]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(f"{path}: invalid JSON: {error}") from error

    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a top-level JSON array")

    return [entry for entry in data if isinstance(entry, dict)]


def extract_attachment_strings(message: dict) -> list[str]:
    strings: list[str] = []
    attachments = message.get("attachments", [])
    if not isinstance(attachments, list):
        return strings

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        for key in ("pretext", "fallback", "text", "title"):
            value = attachment.get(key)
            if isinstance(value, str) and value:
                strings.append(value)
    return strings


def detect_creation(message: dict) -> tuple[ItemKey, str, str] | None:
    attachments = message.get("attachments", [])
    if not isinstance(attachments, list):
        return None

    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        pretext = attachment.get("pretext")
        if not isinstance(pretext, str):
            continue

        creation_match = CREATION_RE.search(pretext)
        if not creation_match:
            continue

        title = attachment.get("title")
        title_link = attachment.get("title_link")
        if not isinstance(title, str) or not isinstance(title_link, str):
            continue

        if "/pull/" in title_link:
            kind = "pull"
        elif "/issues/" in title_link:
            kind = "issues"
        else:
            continue

        number_match = re.search(r"#(?P<number>\d+)", title)
        if not number_match:
            continue

        item_key = ItemKey(kind=kind, number=int(number_match.group("number")))
        clean_title = title.split(" ", 1)[1] if " " in title else title
        owner = creation_match.group("owner").strip()
        return item_key, clean_title.strip(), owner

    return None


def detect_activity(message: dict) -> tuple[ItemKey, str, str] | None:
    candidates: list[str] = []

    text = message.get("text")
    if isinstance(text, str) and text:
        candidates.append(text)

    candidates.extend(extract_attachment_strings(message))

    for candidate in candidates:
        inline_match = INLINE_ACTIVITY_RE.search(candidate)
        if inline_match:
            return build_activity_result(inline_match)

        new_comment_match = NEW_COMMENT_RE.search(candidate)
        if new_comment_match:
            return build_activity_result(new_comment_match)

    return None


def build_activity_result(match: re.Match[str]) -> tuple[ItemKey, str, str]:
    kind = match.group("kind")
    actor = match.group("actor").strip()
    title = match.group("title").strip()
    number = int(match.group("number"))
    return ItemKey(kind=kind, number=number), title, actor


def detect_closed_pr(message: dict) -> tuple[ItemKey, str, str] | None:
    candidates: list[str] = []

    text = message.get("text")
    if isinstance(text, str) and text:
        candidates.append(text)

    candidates.extend(extract_attachment_strings(message))

    for candidate in candidates:
        match = CLOSED_PR_RE.search(candidate)
        if match:
            return (
                ItemKey(kind="pull", number=int(match.group("number"))),
                match.group("title").strip(),
                match.group("actor").strip(),
            )

    return None


def detect_merge_commit(message: dict) -> tuple[ItemKey, str] | None:
    candidates: list[str] = []

    text = message.get("text")
    if isinstance(text, str) and text:
        candidates.append(text)

    candidates.extend(extract_attachment_strings(message))

    for candidate in candidates:
        match = MERGE_COMMIT_RE.search(candidate)
        if match:
            return (
                ItemKey(kind="pull", number=int(match.group("number"))),
                match.group("merger_name").strip(),
            )

    return None


def get_or_create_item(
    items: dict[ItemKey, ItemRecord], key: ItemKey, title: str
) -> ItemRecord:
    item = items.get(key)
    if item is None:
        item = ItemRecord(key=key, title=title)
        items[key] = item
        return item

    if (not item.title or len(item.title) < len(title)) and title:
        item.title = title
    return item


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def format_row(row: list[str]) -> str:
        return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))

    separator = "-+-".join("-" * width for width in widths)
    lines = [format_row(headers), separator]
    lines.extend(format_row(row) for row in rows)
    return "\n".join(lines)


def build_item_rows(items: dict[ItemKey, ItemRecord]) -> list[list[str]]:
    rows: list[list[str]] = []
    for key in sorted(items):
        item = items[key]
        if not item.commenters:
            continue

        commenters = ", ".join(sorted(item.commenters))
        rows.append(
            [
                item.display_type,
                item.display_name,
                item.owner or "UNKNOWN",
                commenters,
                str(len(item.commenters)),
            ]
        )
    return rows


def build_user_rows(items: dict[ItemKey, ItemRecord]) -> list[list[str]]:
    issue_map: dict[str, set[int]] = defaultdict(set)
    pr_map: dict[str, set[int]] = defaultdict(set)
    created_issue_map: dict[str, set[int]] = defaultdict(set)
    created_pr_map: dict[str, set[int]] = defaultdict(set)
    merged_pr_map: dict[str, set[int]] = defaultdict(set)

    for item in items.values():
        if item.owner is not None and not is_bot(item.owner):
            if item.key.kind == "issues":
                created_issue_map[item.owner].add(item.key.number)
            else:
                created_pr_map[item.owner].add(item.key.number)

        if (
            item.key.kind == "pull"
            and item.merged_by is not None
            and not is_bot(item.merged_by)
        ):
            merged_pr_map[item.merged_by].add(item.key.number)

        for commenter in item.commenters:
            if item.key.kind == "issues":
                issue_map[commenter].add(item.key.number)
            else:
                pr_map[commenter].add(item.key.number)

    users = sorted(
        set(issue_map)
        | set(pr_map)
        | set(created_issue_map)
        | set(created_pr_map)
        | set(merged_pr_map),
        key=lambda user: (
            -(
                len(issue_map[user])
                + len(pr_map[user])
                + len(created_issue_map[user])
                + len(created_pr_map[user])
                + len(merged_pr_map[user])
            ),
            -(len(issue_map[user]) + len(pr_map[user])),
            -(len(created_issue_map[user]) + len(created_pr_map[user])),
            -len(merged_pr_map[user]),
            -len(pr_map[user]),
            -len(issue_map[user]),
            user.lower(),
        ),
    )

    rows: list[list[str]] = []
    for user in users:
        company = company_for_user(user)
        issue_count = len(issue_map[user])
        pr_count = len(pr_map[user])
        created_issue_count = len(created_issue_map[user])
        created_pr_count = len(created_pr_map[user])
        merged_pr_count = len(merged_pr_map[user])
        total_count = (
            issue_count
            + pr_count
            + created_issue_count
            + created_pr_count
            + merged_pr_count
        )
        ratio = format_created_pr_ratio(
            issue_count,
            pr_count,
            created_issue_count,
            created_pr_count,
            merged_pr_count,
        )
        rows.append(
            [
                user,
                company,
                str(issue_count),
                str(pr_count),
                str(created_issue_count),
                str(created_pr_count),
                str(merged_pr_count),
                str(total_count),
                ratio,
            ]
        )
    return rows


def build_company_rows(items: dict[ItemKey, ItemRecord]) -> list[list[str]]:
    issue_map: dict[str, set[int]] = defaultdict(set)
    pr_map: dict[str, set[int]] = defaultdict(set)
    created_issue_map: dict[str, set[int]] = defaultdict(set)
    created_pr_map: dict[str, set[int]] = defaultdict(set)
    merged_pr_map: dict[str, set[int]] = defaultdict(set)

    for item in items.values():
        if item.owner is not None and not is_bot(item.owner):
            owner_company = company_for_user(item.owner)
            if item.key.kind == "issues":
                created_issue_map[owner_company].add(item.key.number)
            else:
                created_pr_map[owner_company].add(item.key.number)

        if (
            item.key.kind == "pull"
            and item.merged_by is not None
            and not is_bot(item.merged_by)
        ):
            merged_pr_map[company_for_user(item.merged_by)].add(item.key.number)

        for commenter in item.commenters:
            commenter_company = company_for_user(commenter)
            if item.key.kind == "issues":
                issue_map[commenter_company].add(item.key.number)
            else:
                pr_map[commenter_company].add(item.key.number)

    companies = sorted(
        set(issue_map)
        | set(pr_map)
        | set(created_issue_map)
        | set(created_pr_map)
        | set(merged_pr_map),
        key=lambda company: (
            company == UNMAPPED_COMPANY,
            -(
                len(issue_map[company])
                + len(pr_map[company])
                + len(created_issue_map[company])
                + len(created_pr_map[company])
                + len(merged_pr_map[company])
            ),
            -(len(issue_map[company]) + len(pr_map[company])),
            -(len(created_issue_map[company]) + len(created_pr_map[company])),
            -len(merged_pr_map[company]),
            company.lower(),
        ),
    )

    rows: list[list[str]] = []
    for company in companies:
        issue_count = len(issue_map[company])
        pr_count = len(pr_map[company])
        created_issue_count = len(created_issue_map[company])
        created_pr_count = len(created_pr_map[company])
        merged_pr_count = len(merged_pr_map[company])
        total_count = (
            issue_count
            + pr_count
            + created_issue_count
            + created_pr_count
            + merged_pr_count
        )
        ratio = format_created_pr_ratio(
            issue_count,
            pr_count,
            created_issue_count,
            created_pr_count,
            merged_pr_count,
        )
        rows.append(
            [
                company,
                str(issue_count),
                str(pr_count),
                str(created_issue_count),
                str(created_pr_count),
                str(merged_pr_count),
                str(total_count),
                ratio,
            ]
        )

    return rows


def main() -> int:
    args = parse_args()
    directory = Path(args.directory)

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory", file=sys.stderr)
        return 1

    files = list(iter_export_files(directory))
    if not files:
        print(f"Error: no JSON files found in {directory}", file=sys.stderr)
        return 1

    items: dict[ItemKey, ItemRecord] = {}
    skipped_invalid_files = 0

    for path in files:
        try:
            messages = load_messages(path)
        except ValueError as error:
            print(f"Warning: {error}", file=sys.stderr)
            skipped_invalid_files += 1
            continue

        for message in messages:
            creation = detect_creation(message)
            if creation:
                key, title, owner = creation
                item = get_or_create_item(items, key, title)
                if item.owner is None:
                    item.owner = owner

            closed_pr = detect_closed_pr(message)
            if closed_pr:
                key, title, actor = closed_pr
                item = get_or_create_item(items, key, title)
                item.closed_by = actor
                if item.merge_commit_author is not None:
                    item.merged_by = actor

            merge_commit = detect_merge_commit(message)
            if merge_commit:
                key, merger_name = merge_commit
                item = get_or_create_item(items, key, "")
                item.merge_commit_author = merger_name
                if item.closed_by is not None:
                    item.merged_by = item.closed_by

            activity = detect_activity(message)
            if not activity:
                continue

            key, title, actor = activity
            if not args.include_bots and is_bot(actor):
                continue

            item = get_or_create_item(items, key, title)
            if args.skip_unknown_owner_items and item.owner is None:
                continue

            if item.owner is not None and actor == item.owner:
                continue

            item.commenters.add(actor)

    item_rows = build_item_rows(items)
    user_rows = build_user_rows(items)
    company_rows = build_company_rows(items)

    print(
        f"Processed {len(files)} files from {directory}"
        + (
            f" ({skipped_invalid_files} skipped due to invalid JSON)"
            if skipped_invalid_files
            else ""
        )
    )

    unknown_owner_items = sum(
        1 for item in items.values() if item.commenters and item.owner is None
    )
    print(
        "Items with missing owner in dataset: "
        f"{unknown_owner_items}"
        + (
            " (their self-comments cannot be filtered with certainty)"
            if unknown_owner_items
            else ""
        )
    )
    print()

    print("Issue/PR outside commenters")
    if item_rows:
        print(
            format_table(
                ["Type", "Item", "Owner", "Outside Commenters", "Users"],
                item_rows,
            )
        )
    else:
        print("No outside commentary found.")

    print()
    print("Per-user summary")
    if user_rows:
        print(
            format_table(
                [
                    "User",
                    "Company",
                    "Issues Commented On",
                    "PRs Commented On",
                    "Issues Created",
                    "PRs Created",
                    "PRs Merged",
                    "Total",
                    "Activity/PR Created",
                ],
                user_rows,
            )
        )
    else:
        print("No user summary available.")

    print()
    print("Per-company summary")
    if company_rows:
        print(
            format_table(
                [
                    "Company",
                    "Issues Commented On",
                    "PRs Commented On",
                    "Issues Created",
                    "PRs Created",
                    "PRs Merged",
                    "Total",
                    "Activity/PR Created",
                ],
                company_rows,
            )
        )
    else:
        print("No company summary available.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
