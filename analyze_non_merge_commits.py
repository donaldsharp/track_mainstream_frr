#!/usr/bin/env python3
"""
Script to analyze non-merge commits in a git repository and count who is
making regular commits.

Usage:
    python analyze_non_merge_commits.py --since "2025-01-01"
    python analyze_non_merge_commits.py --since "7 days ago"
    python analyze_non_merge_commits.py --since "2 weeks ago"
"""

import argparse
import subprocess
import sys
from collections import defaultdict
import re
import unicodedata


PERSONAL_EMAIL_DOMAINS = {
    'gmail.com',
    'googlemail.com',
    'hotmail.com',
    'outlook.com',
    'live.com',
    'msn.com',
    'yahoo.com',
    'yahoo.co.uk',
    'icloud.com',
    'me.com',
    'mac.com',
    'proton.me',
    'protonmail.com',
    'pm.me',
    'aol.com',
    'gmx.com',
    'gmx.de',
    'fastmail.com',
    'qq.com',
}

KNOWN_COMPANY_NAMES = {
    'amazon': 'Amazon',
    'arista': 'Arista',
    'cisco': 'Cisco',
    'cumulusnetworks': 'Cumulus Networks',
    'debian': 'Debian',
    'equinix': 'Equinix',
    'frrouting': 'FRRouting',
    'google': 'Google',
    'ibm': 'IBM',
    'intel': 'Intel',
    'labn': 'LabN',
    'linaro': 'Linaro',
    'meta': 'Meta',
    'microsoft': 'Microsoft',
    'netdef': 'NetDEF',
    'nvidia': 'NVIDIA',
    'opensourcerouting': 'Open Source Routing',
    'redhat': 'Red Hat',
    'vmware': 'VMware',
}

# Normalize subsidiaries, acquisitions, and known aliases into the company
# name we want to report.
COMPANY_NAME_ALIASES = {
    'Cumulus Networks': 'NVIDIA',
    'GitHub (noreply)': 'Personal email / unknown company',
    'Jareds Macbook Pro': 'Nether',
    'Lindem': 'LabN',
    'Lost Things': 'Roderickgibson',
    'NetDEF': 'Open Source Routing',
    'Qlyoung': 'NVIDIA',
    'Smartx': 'Paloaltonetworks',
}

# Explicit domain-to-company overrides for cases where generic domain parsing
# is misleading. Keep these easy to extend as more known aliases appear.
EXACT_EMAIL_DOMAIN_COMPANY_OVERRIDES = {
    'gatech.edu': 'College Student',
    'penta-01.mvlab.labs.mlnx': 'NVIDIA',
    'shytyi.net': '6wind',
    'smail.nju.edu.cn': 'College Student',
}

SUFFIX_EMAIL_DOMAIN_COMPANY_OVERRIDES = {
    '.mvlab.labs.mlnx': 'NVIDIA',
}

COMMON_SUBDOMAIN_PREFIXES = {
    'mail',
    'mx',
    'smtp',
    'email',
    'corp',
    'internal',
    'users',
    'dev',
}

MULTIPART_TLDS = {
    'co.uk',
    'org.uk',
    'com.au',
    'com.br',
    'co.jp',
}


def get_non_merge_commits(since_date):
    """
    Get all non-merge commits since the specified date.

    Args:
        since_date: Date string for git log --since parameter

    Returns:
        Raw git log output as a string
    """
    try:
        cmd = [
            'git', 'log',
            '--no-merges',
            '--since', since_date,
            '--format=%H%n%an%n%ae%n%ad%n%s%n---COMMIT_END---'
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Error running git log: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: git command not found. Make sure git is installed.", file=sys.stderr)
        sys.exit(1)


def infer_company_from_email(email):
    """
    Infer a likely company from an email address.

    This is heuristic only: personal email providers are labeled as personal,
    and company names are derived from the email domain when possible.
    """
    if '@' not in email:
        return 'Unknown'

    # Some commit emails contain an extra '@' in the local part. Use the last
    # separator so addresses like "name@123@qq.com" still resolve to "qq.com".
    domain = email.rsplit('@', 1)[1].lower().strip()
    if not domain:
        return 'Unknown'

    overridden_company = lookup_company_override(domain)
    if overridden_company:
        return overridden_company

    if domain in {'users.noreply.github.com', 'noreply.github.com'}:
        return 'GitHub (noreply)'

    if domain in PERSONAL_EMAIL_DOMAINS:
        return 'Personal email / unknown company'

    registered_domain = extract_registered_domain(domain)
    if not registered_domain:
        return canonicalize_company_name(domain)

    company_key = registered_domain.split('.', 1)[0]
    return canonicalize_company_name(format_company_name(company_key))


def canonicalize_company_name(company_name):
    """
    Map known aliases or acquired brands to a canonical company name.
    """
    return COMPANY_NAME_ALIASES.get(company_name, company_name)


def lookup_company_override(domain):
    """
    Return an explicit company mapping for known special-case email domains.
    """
    if domain in EXACT_EMAIL_DOMAIN_COMPANY_OVERRIDES:
        return canonicalize_company_name(
            EXACT_EMAIL_DOMAIN_COMPANY_OVERRIDES[domain]
        )

    for suffix, company in SUFFIX_EMAIL_DOMAIN_COMPANY_OVERRIDES.items():
        if domain.endswith(suffix):
            return canonicalize_company_name(company)

    return None


def extract_registered_domain(domain):
    """
    Best-effort extraction of the registered domain without external deps.
    """
    parts = domain.split('.')
    if len(parts) < 2:
        return domain

    suffix = '.'.join(parts[-2:])
    if suffix in MULTIPART_TLDS and len(parts) >= 3:
        return '.'.join(parts[-3:])

    if parts[0] in COMMON_SUBDOMAIN_PREFIXES and len(parts) > 2:
        return '.'.join(parts[-2:])

    return '.'.join(parts[-2:])


def format_company_name(company_key):
    """
    Convert a domain label into a readable company name.
    """
    if company_key in KNOWN_COMPANY_NAMES:
        return KNOWN_COMPANY_NAMES[company_key]

    words = [word for word in re.split(r'[-_]+', company_key) if word]
    if not words:
        return company_key

    formatted_words = []
    for word in words:
        if word.upper() in {'IBM', 'AWS', 'AMD', 'NVIDIA', 'FRR'}:
            formatted_words.append(word.upper())
        else:
            formatted_words.append(word.capitalize())

    return ' '.join(formatted_words)


def clean_author_name(name):
    """
    Normalize whitespace and trim obvious suffix noise from an author name.
    """
    cleaned = ' '.join(name.split()).strip()
    cleaned = re.sub(r"'s$", '', cleaned, flags=re.IGNORECASE)
    return cleaned


def normalize_author_key(name):
    """
    Create a comparison key that merges trivial case/spacing/punctuation variants.
    """
    cleaned = clean_author_name(name)
    normalized = unicodedata.normalize('NFKD', cleaned)
    normalized = ''.join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    normalized = normalized.casefold()
    normalized = re.sub(r'[^a-z0-9]+', '', normalized)
    return normalized or cleaned.casefold()


def choose_display_name(name_counts):
    """
    Pick the best display name from the observed variants for one author.
    """
    sorted_names = sorted(
        name_counts.items(),
        key=lambda x: (
            -x[1],
            -sum(1 for char in x[0] if char.isupper()),
            x[0].casefold()
        )
    )
    name = sorted_names[0][0]

    if re.fullmatch(r'[a-z]+(?: [a-z]+)*', name):
        return name.title()

    return name


def choose_primary_company(company_counts):
    """
    Pick the most likely company for an author across all of their commits.
    """
    low_confidence_companies = {
        'GitHub (noreply)',
        'Personal email / unknown company',
        'Unknown',
    }

    sorted_companies = sorted(
        company_counts.items(),
        key=lambda x: (
            -x[1],
            x[0] in low_confidence_companies,
            x[0]
        )
    )
    return sorted_companies[0][0]


def format_table_cell(value, width):
    """
    Format a table cell and truncate with ellipsis if needed.
    """
    text = str(value)
    if len(text) <= width:
        return f"{text:<{width}}"

    if width <= 3:
        return text[:width]

    return f"{text[:width - 3]}..."


def parse_non_merge_commits(git_output):
    """
    Parse the git log output and extract non-merge commit information.

    Args:
        git_output: Raw output from git log command

    Returns:
        Dictionary mapping normalized author identities to commit stats and
        commit details
    """
    author_stats = {}
    commit_details = []

    commits = git_output.split('---COMMIT_END---')

    for commit in commits:
        commit = commit.strip()
        if not commit:
            continue

        lines = commit.split('\n')
        if len(lines) >= 5:
            commit_hash = lines[0]
            author_name = clean_author_name(lines[1])
            author_email = lines[2]
            commit_date = lines[3]
            subject = lines[4]
            company = infer_company_from_email(author_email)
            author_key = normalize_author_key(author_name)

            if author_key not in author_stats:
                author_stats[author_key] = {
                    'count': 0,
                    'name_counts': defaultdict(int),
                    'email_counts': defaultdict(int),
                    'company_counts': defaultdict(int),
                }

            author_stats[author_key]['count'] += 1
            author_stats[author_key]['name_counts'][author_name] += 1
            author_stats[author_key]['email_counts'][author_email] += 1
            author_stats[author_key]['company_counts'][company] += 1

            commit_details.append({
                'hash': commit_hash[:12],
                'author': author_name,
                'author_key': author_key,
                'email': author_email,
                'company': company,
                'date': commit_date,
                'subject': subject
            })

    return author_stats, commit_details


def display_results(author_stats, commit_details, since_date, show_details=False):
    """
    Display the non-merge commit analysis results.

    Args:
        author_stats: Dictionary mapping normalized author identities to stats
        commit_details: List of commit details
        since_date: The date filter used
        show_details: Whether to show detailed commit information
    """
    print(f"\n{'='*80}")
    print("Non-Merge Commit Analysis")
    print(f"Since: {since_date}")
    print(f"{'='*80}\n")

    if not author_stats:
        print("No non-merge commits found in the specified time period.")
        return

    author_summaries = []
    for stats in author_stats.values():
        author_summaries.append({
            'author': choose_display_name(stats['name_counts']),
            'company': choose_primary_company(stats['company_counts']),
            'count': stats['count'],
        })

    sorted_authors = sorted(
        author_summaries,
        key=lambda x: (-x['count'], x['author'].casefold())
    )

    total_commits = 0
    for summary in sorted_authors:
        total_commits += summary['count']

    author_width = min(
        max(28, len('Author'), max(len(summary['author']) for summary in sorted_authors)),
        36
    )
    company_width = min(
        max(32, len('Company'), max(len(summary['company']) for summary in sorted_authors)),
        40
    )

    print(
        f"{'Author':<{author_width}} {'Company':<{company_width}} "
        f"{'Commit Count':>12} {'Percent':>8}"
    )
    print(f"{'-'*author_width} {'-'*company_width} {'-'*12} {'-'*8}")

    for summary in sorted_authors:
        percent = (summary['count'] / total_commits) * 100
        print(
            f"{format_table_cell(summary['author'], author_width)} "
            f"{format_table_cell(summary['company'], company_width)} "
            f"{summary['count']:>12} {percent:>7.1f}%"
        )

    print(f"{'-'*author_width} {'-'*company_width} {'-'*12} {'-'*8}")
    print(
        f"{format_table_cell('TOTAL', author_width)} "
        f"{format_table_cell('', company_width)} "
        f"{total_commits:>12} {'100.0%':>8}"
    )

    all_companies = set()
    for stats in author_stats.values():
        all_companies.update(stats['company_counts'].keys())

    print(f"\nTotal unique normalized authors: {len(author_stats)}")
    print(f"Total inferred companies: {len(all_companies)}")

    if show_details:
        print(f"\n{'='*80}")
        print("Detailed Non-Merge Commit List")
        print(f"{'='*80}\n")

        for detail in commit_details:
            print(f"Commit:  {detail['hash']}")
            print(f"Author:  {detail['author']} <{detail['email']}>")
            print(f"Company: {detail['company']}")
            print(f"Date:    {detail['date']}")
            print(f"Subject: {detail['subject']}")
            print()

    company_counts = defaultdict(int)
    for stats in author_stats.values():
        for company, count in stats['company_counts'].items():
            company_counts[company] += count

    sorted_companies = sorted(
        company_counts.items(),
        key=lambda x: (-x[1], x[0])
    )

    print(f"\n{'='*80}")
    print("Company Totals")
    print(f"{'='*80}\n")

    company_totals_width = min(
        max(40, len('Company'), max(len(company) for company, _count in sorted_companies)),
        56
    )

    print(f"{'Company':<{company_totals_width}} {'Commit Count':>12} {'Percent':>8}")
    print(f"{'-'*company_totals_width} {'-'*12} {'-'*8}")

    for company, count in sorted_companies:
        percent = (count / total_commits) * 100
        print(
            f"{format_table_cell(company, company_totals_width)} "
            f"{count:>12} {percent:>7.1f}%"
        )

    print(f"{'-'*company_totals_width} {'-'*12} {'-'*8}")
    print(
        f"{format_table_cell('TOTAL', company_totals_width)} "
        f"{sum(company_counts.values()):>12} {'100.0%':>8}"
    )


def main():
    parser = argparse.ArgumentParser(
        description='Analyze non-merge commits in a git repository, count commits per author, and infer company from email domain.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --since "2026-01-01"
  %(prog)s --since "7 days ago"
  %(prog)s --since "2 weeks ago"
  %(prog)s --since "1 month ago" --details
        """
    )

    parser.add_argument(
        '--since',
        required=True,
        help='Show commits since this date (e.g., "2026-01-01", "7 days ago", "2 weeks ago")'
    )

    parser.add_argument(
        '--details',
        action='store_true',
        help='Show detailed information about each non-merge commit'
    )

    args = parser.parse_args()

    print(f"Analyzing non-merge commits since {args.since}...")
    git_output = get_non_merge_commits(args.since)

    author_stats, commit_details = parse_non_merge_commits(git_output)

    display_results(author_stats, commit_details, args.since, args.details)


if __name__ == '__main__':
    main()
