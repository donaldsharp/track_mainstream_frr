#!/usr/bin/env python3
"""
Script to analyze merge commits in a git repository and count who is merging code.

Usage:
    python analyze_merge_commits.py --since "2025-01-01"
    python analyze_merge_commits.py --since "7 days ago"
    python analyze_merge_commits.py --since "2 weeks ago"
"""

import subprocess
import argparse
import sys
import re
from collections import defaultdict


def get_merge_commits(since_date):
    """
    Get all merge commits since the specified date.
    
    Args:
        since_date: Date string for git log --since parameter
        
    Returns:
        List of commit information dictionaries
    """
    try:
        # Run git log to get merge commits
        cmd = [
            'git', 'log',
            '--merges',  # Only show merge commits
            '--since', since_date,
            '--format=%H%n%P%n%an%n%ae%n%ad%n%s%n---COMMIT_END---'
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


def parse_merge_commits(git_output):
    """
    Parse the git log output and extract merge commit information.
    
    Args:
        git_output: Raw output from git log command
        
    Returns:
        Dictionary mapping author names to merge counts
    """
    merge_counts = defaultdict(int)
    merge_details = []
    
    # Split by commit separator
    commits = git_output.split('---COMMIT_END---')
    
    for commit in commits:
        commit = commit.strip()
        if not commit:
            continue
            
        lines = commit.split('\n')
        if len(lines) >= 6:
            commit_hash = lines[0]
            parents = lines[1]
            author_name = lines[2]
            author_email = lines[3]
            commit_date = lines[4]
            subject = lines[5]
            
            # Increment merge count for this author
            merge_counts[author_name] += 1
            
            # Store details for later display
            merge_details.append({
                'hash': commit_hash[:12],
                'author': author_name,
                'email': author_email,
                'date': commit_date,
                'subject': subject
            })
    
    return merge_counts, merge_details


def display_results(merge_counts, merge_details, since_date, show_details=False):
    """
    Display the merge commit analysis results.
    
    Args:
        merge_counts: Dictionary mapping author names to merge counts
        merge_details: List of merge commit details
        since_date: The date filter used
        show_details: Whether to show detailed commit information
    """
    print(f"\n{'='*80}")
    print(f"Merge Commit Analysis")
    print(f"Since: {since_date}")
    print(f"{'='*80}\n")
    
    if not merge_counts:
        print("No merge commits found in the specified time period.")
        return
    
    # Sort by merge count (descending) then by name
    sorted_mergers = sorted(
        merge_counts.items(),
        key=lambda x: (-x[1], x[0])
    )
    
    print(f"{'Author':<40} {'Merge Count':>12}")
    print(f"{'-'*40} {'-'*12}")
    
    total_merges = 0
    for author, count in sorted_mergers:
        print(f"{author:<40} {count:>12}")
        total_merges += count
    
    print(f"{'-'*40} {'-'*12}")
    print(f"{'TOTAL':<40} {total_merges:>12}")
    
    print(f"\nTotal unique mergers: {len(merge_counts)}")
    
    if show_details:
        print(f"\n{'='*80}")
        print("Detailed Merge Commit List")
        print(f"{'='*80}\n")
        
        # Sort details by date (most recent first)
        for detail in merge_details:
            print(f"Commit:  {detail['hash']}")
            print(f"Author:  {detail['author']} <{detail['email']}>")
            print(f"Date:    {detail['date']}")
            print(f"Subject: {detail['subject']}")
            print()


def main():
    parser = argparse.ArgumentParser(
        description='Analyze merge commits in a git repository and count merges per author.',
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
        help='Show detailed information about each merge commit'
    )
    
    args = parser.parse_args()
    
    # Get merge commits from git
    print(f"Analyzing merge commits since {args.since}...")
    git_output = get_merge_commits(args.since)
    
    # Parse the output
    merge_counts, merge_details = parse_merge_commits(git_output)
    
    # Display results
    display_results(merge_counts, merge_details, args.since, args.details)


if __name__ == '__main__':
    main()
