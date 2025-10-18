#!/usr/bin/env python3
"""
Analyze CI builds over a time period and provide statistics on failures.
The script analyzes builds from N days before the specified build's completion time.

Usage: ./analyze_ci.py <build_number> [days]
Examples: 
  ./analyze_ci.py 9083          # Analyze 7 days before build 9083's completion (default)
  ./analyze_ci.py 9083 14       # Analyze 14 days before build 9083's completion
  ./analyze_ci.py 9059 7        # Analyze 7 days before build 9059's completion
"""

import sys
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup

# Import the parsing function from check_ci_build
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from check_ci_build import parse_build_status


def download_page_safe(url):
    """Download page safely without sys.exit on error."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error downloading page: {e}")


def get_builds_from_week(latest_build_num, days=7):
    """
    Get build numbers from the past week relative to the specified build.
    We'll go backwards from latest_build_num and check dates.
    """
    builds = []
    current_num = latest_build_num

    print(f"Analyzing builds from {days} days before build #{current_num}...")
    print(f"Fetching reference build #{current_num} to get completion time...")
    print()

    # First, fetch the reference build to get its completion date
    reference_url = f"https://ci1.netdef.org/browse/FRR-FRR-{current_num}"
    reference_date = None

    try:
        html = download_page_safe(reference_url)
        results = parse_build_status(html, reference_url)

        if results["completed_time"]:
            # Parse date like "17 Oct 2025, 1:43:42 PM"
            try:
                date_str = results["completed_time"]
                # Extract just the date part
                date_match = re.match(r"(\d{1,2}\s+\w+\s+\d{4})", date_str)
                if date_match:
                    date_part = date_match.group(1)
                    reference_date = datetime.strptime(date_part, "%d %b %Y")
                    print(f"Reference build completed: {date_part}")
            except Exception as e:
                print(f"Warning: Could not parse reference date: {e}")
    except Exception as e:
        print(f"Error: Could not fetch reference build #{current_num}: {e}")
        return builds

    if not reference_date:
        print("Error: Could not determine reference build completion date")
        return builds

    # Calculate cutoff date (days before the reference build)
    cutoff_date = reference_date - timedelta(days=days)
    print(
        f"Analyzing builds from {cutoff_date.strftime('%d %b %Y')} to {reference_date.strftime('%d %b %Y')}"
    )
    print()

    # Go back up to 200 builds or until we hit the date cutoff
    for i in range(200):
        build_num = current_num - i
        if build_num < 1:
            break

        url = f"https://ci1.netdef.org/browse/FRR-FRR-{build_num}"

        try:
            html = download_page_safe(url)
            results = parse_build_status(html, url)

            # Parse the completion date to check if within range
            if results["completed_time"]:
                # Parse date like "17 Oct 2025, 1:43:42 PM"
                try:
                    date_str = results["completed_time"]
                    # Extract just the date part
                    date_match = re.match(r"(\d{1,2}\s+\w+\s+\d{4})", date_str)
                    if date_match:
                        date_part = date_match.group(1)
                        build_date = datetime.strptime(date_part, "%d %b %Y")

                        if build_date < cutoff_date:
                            print(
                                f"Reached builds older than cutoff date at #{build_num} ({date_part})"
                            )
                            break
                except Exception as e:
                    pass  # Continue if date parsing fails

            builds.append({"number": build_num, "url": url, "results": results})

            # Print progress
            if (i + 1) % 10 == 0:
                print(f"Processed {i + 1} builds...")

        except Exception as e:
            print(f"Warning: Could not fetch build #{build_num}: {e}")
            continue

    print(f"\nAnalyzed {len(builds)} builds total")
    return builds


def normalize_job_name(job_name):
    """Normalize job names to help match similar names.

    Examples:
        "IPv4 LDP Protocol on Debian 12" -> "ldp debian 12"
        "LDP Testing on Debian 12" -> "ldp debian 12"
    """
    # Convert to lowercase and extract key words
    normalized = job_name.lower()
    # Remove common words that don't help with matching
    for word in [
        "testing",
        "protocol",
        "on",
        "the",
        "test",
        "tests",
        "ipv4",
        "ipv6",
        "basic",
    ]:
        normalized = normalized.replace(word, "")
    # Collapse multiple spaces
    normalized = " ".join(normalized.split())
    return normalized


def jobs_match(job1_normalized, job2_normalized):
    """Check if two normalized job names likely refer to the same job.

    Returns True if one contains the other or they share significant overlap.
    """
    # If one is a substring of the other, they match
    if job1_normalized in job2_normalized or job2_normalized in job1_normalized:
        return True

    # If they share significant words, they might match
    words1 = set(job1_normalized.split())
    words2 = set(job2_normalized.split())

    # Remove very common words that don't help distinguish jobs
    common_words = {"part", "build", "amd64", "arm8", "i386"}
    words1 -= common_words
    words2 -= common_words

    if not words1 or not words2:
        return False

    # If they share most of their words (>= 66%), consider them matching
    shared_words = words1 & words2
    min_words = min(len(words1), len(words2))

    if min_words > 0 and len(shared_words) / min_words >= 0.66:
        return True

    return False


def analyze_builds(builds):
    """Analyze builds and generate statistics."""

    stats = {
        "total": len(builds),
        "successful": 0,
        "failed": 0,
        "combined_failures": defaultdict(
            int
        ),  # "job - test_case" -> count (kept for backwards compat)
        "test_failures": defaultdict(
            lambda: {"count": 0, "jobs": defaultdict(int), "builds": set()}
        ),  # test_name -> {count, jobs: {job_name: count}, builds: set of build numbers}
        "hung_jobs": defaultdict(int),  # job_name -> count (jobs without test context)
        "error_types": defaultdict(int),  # error pattern -> count
        "builds_by_status": defaultdict(list),  # status -> [build_numbers]
    }

    for build in builds:
        results = build["results"]
        build_num = build["number"]

        if results["status"] == "SUCCESS":
            stats["successful"] += 1
            stats["builds_by_status"]["SUCCESS"].append(build_num)
        else:
            stats["failed"] += 1
            stats["builds_by_status"]["FAILED"].append(build_num)

        # Track which jobs had test failures (using normalized names)
        jobs_with_test_failures_normalized = set()

        # Count test failures (combined with job name)
        for failure in results["new_failures"]:
            test_case = failure["case"]
            job_name = failure.get("job", "Unknown Job")

            # Create combined key: "Job Name - Test Case"
            combined_key = f"{job_name} - {test_case}"
            stats["combined_failures"][combined_key] += 1

            # NEW: Group by test name first
            stats["test_failures"][test_case]["count"] += 1
            stats["test_failures"][test_case]["jobs"][job_name] += 1
            stats["test_failures"][test_case]["builds"].add(build_num)

            # Track normalized job name to avoid double-counting
            jobs_with_test_failures_normalized.add(normalize_job_name(job_name))

            # Categorize error types
            error = failure.get("error", "")
            if "AssertionError" in error:
                stats["error_types"]["AssertionError"] += 1
            elif "RFC" in error or "MUST" in error:
                stats["error_types"]["RFC Compliance"] += 1
            elif "timeout" in error.lower() or "hung" in error.lower():
                stats["error_types"]["Timeout/Hung"] += 1
            elif error:
                stats["error_types"]["Other Error"] += 1

        for failure in results["existing_failures"]:
            test_case = failure["case"]
            job_name = failure.get("job", "Unknown Job")

            # Create combined key: "Job Name - Test Case"
            combined_key = f"{job_name} - {test_case}"
            stats["combined_failures"][combined_key] += 1

            # NEW: Group by test name first
            stats["test_failures"][test_case]["count"] += 1
            stats["test_failures"][test_case]["jobs"][job_name] += 1
            stats["test_failures"][test_case]["builds"].add(build_num)

            # Track normalized job name to avoid double-counting
            jobs_with_test_failures_normalized.add(normalize_job_name(job_name))

        # Count job failures that don't have test context (hung jobs, build failures without test failures)
        for job in results["failed_jobs"]:
            job_name = job["name"]
            job_normalized = normalize_job_name(job_name)

            # If this job is Unknown status (hung), track as a test failure type
            if job["status"] == "Unknown":
                stats["hung_jobs"][job_name] += 1
                # Also add to test_failures for unified reporting
                stats["test_failures"]["(Hung/Timeout)"]["count"] += 1
                stats["test_failures"]["(Hung/Timeout)"]["jobs"][job_name] += 1
                stats["test_failures"]["(Hung/Timeout)"]["builds"].add(build_num)
            else:
                # Check if this job matches any job that already has test failures
                already_tracked = False
                for tracked_job_normalized in jobs_with_test_failures_normalized:
                    if jobs_match(job_normalized, tracked_job_normalized):
                        already_tracked = True
                        break

                # Only add if not already tracked via test failures
                if not already_tracked:
                    combined_key = f"{job_name} - (Job Failed)"
                    stats["combined_failures"][combined_key] += 1

                    # NEW: Group by "(Job Failed)" test name
                    stats["test_failures"]["(Job Failed)"]["count"] += 1
                    stats["test_failures"]["(Job Failed)"]["jobs"][job_name] += 1
                    stats["test_failures"]["(Job Failed)"]["builds"].add(build_num)

    return stats


def print_statistics(stats):
    """Print formatted statistics."""

    print("\n" + "=" * 80)
    print("CI BUILDS ANALYSIS SUMMARY")
    print("=" * 80)

    # Overall statistics
    print(f"\nTotal Builds Analyzed: {stats['total']}")
    print(
        f"Successful:            {stats['successful']} ({stats['successful']/stats['total']*100:.1f}%)"
    )
    print(
        f"Failed:                {stats['failed']} ({stats['failed']/stats['total']*100:.1f}%)"
    )

    # Success rate
    if stats["total"] > 0:
        success_rate = (stats["successful"] / stats["total"]) * 100
        print(f"\nSuccess Rate:          {success_rate:.1f}%")

    # Calculate total failure instances
    if stats["test_failures"]:
        total_failure_instances = sum(
            f["count"] for f in stats["test_failures"].values()
        )
        unique_failures = len(stats["test_failures"])
        print(f"\nTotal Failure Instances: {total_failure_instances}")
        print(f"Unique Test Types:       {unique_failures}")
        if stats["failed"] > 0:
            avg_failures_per_build = total_failure_instances / stats["failed"]
            print(f"Avg Failures per Failed Build: {avg_failures_per_build:.1f}")

    # Test failures grouped by test name
    if stats["test_failures"]:
        print("\n" + "=" * 80)
        print("TOP FAILURES (by test name, with affected systems)")
        print("=" * 80)

        sorted_tests = sorted(
            stats["test_failures"].items(), key=lambda x: x[1]["count"], reverse=True
        )
        for i, (test_name, test_data) in enumerate(sorted_tests[:20], 1):
            count = test_data["count"]
            num_builds_affected = len(test_data["builds"])
            percentage = (num_builds_affected / stats["total"]) * 100
            print(
                f"\n{i:2d}. {test_name} - {count} failures in {num_builds_affected} builds ({percentage:.1f}%)"
            )

            # Sort jobs by frequency for this test
            sorted_jobs = sorted(
                test_data["jobs"].items(), key=lambda x: x[1], reverse=True
            )
            for job_name, job_count in sorted_jobs:
                print(f"    • {job_name} ({job_count}x)")

    # Error types
    if stats["error_types"]:
        print("\n" + "=" * 80)
        print("ERROR TYPES")
        print("=" * 80)

        sorted_errors = sorted(
            stats["error_types"].items(), key=lambda x: x[1], reverse=True
        )
        for error_type, count in sorted_errors:
            print(f"  • {error_type:30s} - {count:3d} occurrences")

    # Build number ranges
    print("\n" + "=" * 80)
    print("BUILD RANGE")
    print("=" * 80)

    # Get all build numbers
    all_builds = (
        stats["builds_by_status"]["SUCCESS"] + stats["builds_by_status"]["FAILED"]
    )
    if all_builds:
        min_build = min(all_builds)
        max_build = max(all_builds)
        print(f"Analyzed builds: #{min_build} - #{max_build}")

    print("\n" + "=" * 80)


def print_detailed_failures(builds):
    """Print detailed failure information grouped by failure type."""

    # Group builds by their failures
    failures_to_builds = defaultdict(list)

    for build in builds:
        results = build["results"]
        build_num = build["number"]

        if results["status"] != "SUCCESS":
            # Create a signature for this build's failures
            failure_signature = []

            # Track jobs with test failures
            jobs_with_tests = set()

            for failure in results["new_failures"]:
                job_name = failure.get("job", "Unknown Job")
                test_case = failure["case"]
                failure_signature.append(("combined", f"{job_name} - {test_case}"))
                jobs_with_tests.add(job_name)

            for failure in results["existing_failures"]:
                job_name = failure.get("job", "Unknown Job")
                test_case = failure["case"]
                failure_signature.append(("combined", f"{job_name} - {test_case}"))
                jobs_with_tests.add(job_name)

            # Only add jobs that don't have test context
            for job in results["failed_jobs"]:
                job_name = job["name"]
                if job["status"] == "Unknown":
                    failure_signature.append(("hung", job_name))
                elif job_name not in jobs_with_tests:
                    failure_signature.append(("job_only", job_name))

            # Sort for consistent grouping
            failure_signature = tuple(sorted(failure_signature))

            failures_to_builds[failure_signature].append(build_num)

    if failures_to_builds:
        print("\n" + "=" * 80)
        print("FAILURE PATTERNS (builds with same failures grouped)")
        print("=" * 80)

        # Sort by number of builds affected
        sorted_patterns = sorted(
            failures_to_builds.items(), key=lambda x: len(x[1]), reverse=True
        )

        for i, (pattern, build_nums) in enumerate(sorted_patterns[:10], 1):
            print(f"\nPattern #{i}: Affects {len(build_nums)} builds")
            print(f"Builds: {', '.join(f'#{n}' for n in sorted(build_nums))}")
            print("Failures:")

            for item in pattern:
                if item[0] == "combined":
                    print(f"  ✗ {item[1]}")
                elif item[0] == "hung":
                    print(f"  ✗ Hung: {item[1]}")
                elif item[0] == "job_only":
                    print(f"  ✗ Job: {item[1]}")


def main():
    """Main function."""
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: ./analyze_ci.py <build_number> [days]")
        print("Example: ./analyze_ci.py 9083")
        print("         ./analyze_ci.py 9083 14  (analyze 14 days before build 9083)")
        print("         ./analyze_ci.py 9059 7   (analyze 7 days before build 9059)")
        print(
            "\nThe script analyzes builds from N days before the specified build's completion time."
        )
        print("\nOptional parameters:")
        print("  days: Number of days to look back from the build (default: 7)")
        sys.exit(1)

    try:
        latest_build = int(sys.argv[1])
    except ValueError:
        print("Error: Build number must be an integer")
        sys.exit(1)

    # Get optional days parameter
    days = 7  # Default
    if len(sys.argv) == 3:
        try:
            days = int(sys.argv[2])
            if days < 1:
                print("Error: Days must be a positive integer")
                sys.exit(1)
        except ValueError:
            print("Error: Days parameter must be an integer")
            sys.exit(1)

    # Get builds from the specified time period
    builds = get_builds_from_week(latest_build, days=days)

    if not builds:
        print("No builds found to analyze")
        sys.exit(1)

    # Analyze the builds
    stats = analyze_builds(builds)

    # Print statistics
    print_statistics(stats)

    # Print detailed failure patterns
    print_detailed_failures(builds)

    print("\nAnalysis complete!")


if __name__ == "__main__":
    main()
