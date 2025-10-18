#!/usr/bin/env python3
"""
Script to check FRR CI build status and report failures.
Usage: ./check_ci_build.py <build_url>
Example: ./check_ci_build.py https://ci1.netdef.org/browse/FRR-FRR-9082
"""

import sys
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_test_case_name(test_string):
    """Extract the test case name from a test string.
    
    Examples:
        'RFC-Compliance-tests [ANVL-LDP-9.5]' -> 'ANVL-LDP-9.5'
        'test_isis_srv6_topo1 [test_rib_ipv6_step3]' -> 'test_rib_ipv6_step3'
    """
    # Look for text in brackets
    match = re.search(r'\[([^\]]+)\]', test_string)
    if match:
        return match.group(1)
    # If no brackets, return the whole string
    return test_string


def extract_test_suite_and_case(test_string):
    """Extract both test suite and test case from a test string.
    
    Returns: (suite_name, case_name)
    """
    # Look for pattern: "Suite Name [Case Name]"
    match = re.match(r'^(.+?)\s*\[([^\]]+)\]', test_string)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    # No brackets found, treat whole string as case name
    return None, test_string.strip()


def download_page(url):
    """Download the HTML content of the given URL."""
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Error downloading page: {e}")
        sys.exit(1)


def parse_build_status(html_content, url):
    """Parse the HTML and extract build status and failure information."""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    results = {
        'url': url,
        'build_number': None,
        'status': 'Unknown',
        'completed_time': None,
        'new_failures': [],
        'existing_failures': [],
        'fixed_tests': [],
        'failed_jobs': [],
        'quarantined_skipped': 0,
        'total_tests': 0
    }
    
    # Extract build number from URL if present
    url_match = re.search(r'FRR-FRR-(\d+)', url)
    if url_match:
        results['build_number'] = f"#{url_match.group(1)}"
    
    # Look for build status in the HTML - try multiple methods
    # Method 1: Look for h1 with build info (most reliable)
    build_heading = soup.find('h1')
    if build_heading:
        heading_text = build_heading.get_text()
        # Extract build number with potential comma separator
        build_num_match = re.search(r'#[\d,]+', heading_text)
        if build_num_match:
            results['build_number'] = build_num_match.group(0)
        
        # Check for status keywords in the heading
        # Look for pattern like "Build: #9081 failed" or "Build #9082 successful"
        heading_lower = heading_text.lower()
        # Use word boundaries to avoid false matches
        if re.search(r'\bfailed\b', heading_lower):
            results['status'] = 'FAILED'
        elif re.search(r'\b(successful|success)\b', heading_lower):
            results['status'] = 'SUCCESS'
    
    # Method 2: Look for "Build result summary" or similar sections
    if results['status'] == 'Unknown':
        summary_heading = soup.find('h1', string=re.compile(r'Build', re.I))
        if not summary_heading:
            summary_heading = soup.find(string=re.compile(r'Build.*#\d+.*(failed|successful)', re.I))
        
        if summary_heading:
            summary_text = summary_heading if isinstance(summary_heading, str) else summary_heading.get_text()
            if re.search(r'\bfailed\b', summary_text, re.I):
                results['status'] = 'FAILED'
            elif re.search(r'\b(successful|success)\b', summary_text, re.I):
                results['status'] = 'SUCCESS'
    
    # Method 3: Check if there are any test failures listed (indicates failed build)
    if results['status'] == 'Unknown':
        # Look for text like "New test failures 1" or "Existing test failures 27"
        page_text = soup.get_text()
        new_failures_match = re.search(r'New test failures\s+(\d+)', page_text, re.I)
        if new_failures_match and int(new_failures_match.group(1)) > 0:
            results['status'] = 'FAILED'
        
        # Also check for existing test failures
        existing_failures_match = re.search(r'Existing test failures\s+(\d+)', page_text, re.I)
        if existing_failures_match and int(existing_failures_match.group(1)) > 0:
            results['status'] = 'FAILED'
    
    # Method 4: Check for "Failing since" indicator
    if results['status'] == 'Unknown':
        failing_since = soup.find('dt', class_='failing-since')
        if failing_since:
            results['status'] = 'FAILED'
    
    # Method 5: If we found test failures during parsing, mark as failed
    # This will be checked after parsing tests
    
    # Extract completion time
    completed_dt = soup.find('dt', class_='completed')
    if completed_dt:
        completed_dd = completed_dt.find_next_sibling('dd')
        if completed_dd:
            time_elem = completed_dd.find('time')
            if time_elem:
                # Get the text content for display (e.g., "17 Oct 2025, 1:43:42 PM")
                time_text = time_elem.get_text(strip=True)
                # Remove the "ago" part if present (e.g., "– 18 hours ago")
                time_text = re.sub(r'\s*–\s*.*$', '', time_text)
                results['completed_time'] = time_text
    
    # Try to find test statistics - search in all text
    page_text = soup.get_text()
    
    # Look for patterns like "Total tests 21832"
    total_match = re.search(r'Total tests[:\s]+(\d+)', page_text, re.IGNORECASE)
    if total_match:
        results['total_tests'] = int(total_match.group(1))
    
    # Look for quarantined/skipped
    quarantine_match = re.search(r'(\d+)\s+Quarantined\s*/\s*skipped', page_text, re.IGNORECASE)
    if quarantine_match:
        results['quarantined_skipped'] = int(quarantine_match.group(1))
    
    # Parse new test failures - look for the heading and the table
    # Try to find tables containing test information
    all_tables = soup.find_all('table')
    
    for table in all_tables:
        # Check if this table has test failure information
        # Look for header row with "Status", "Test", "View job"
        header_row = table.find('tr')
        if header_row:
            header_text = header_row.get_text().lower()
            # This looks like a test results table
            if 'status' in header_text and 'test' in header_text:
                rows = table.find_all('tr')[1:]  # Skip header row
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 2:
                        # Bamboo test tables typically have:
                        # Column 0: Status (with collapse/expand button)
                        # Column 1: Test name (with link to test details)
                        # Column 2: "View job" or similar link
                        # Column 3: Duration (optional)
                        
                        # First, check if this is a failing test by looking at status column
                        status_cell = cells[0]
                        status_text = status_cell.get_text(strip=True).lower()
                        is_failure = 'fail' in status_text or 'collapse' in status_text
                        
                        if not is_failure:
                            continue
                        
                        # Extract test name from column 1 or 2 depending on header count
                        # Bamboo uses column 2 for test (col 0=collapse, col 1=status, col 2=test)
                        test_cell = cells[2] if len(cells) >= 3 else cells[1]
                        
                        # Try to extract test suite and case from Bamboo's structure
                        test_suite_span = test_cell.find('span', class_='test-class')
                        test_name_link = test_cell.find('a', class_='test-name')
                        
                        if test_suite_span and test_name_link:
                            # Found both suite and case name
                            suite_name = test_suite_span.get_text(strip=True)
                            case_name = test_name_link.get_text(strip=True)
                            test_name = f"{suite_name} [{case_name}]"
                        elif test_name_link:
                            # Only found case name
                            case_name = test_name_link.get_text(strip=True)
                            suite_name = None
                            test_name = case_name
                        else:
                            # Fallback to getting all text
                            test_name = test_cell.get_text(strip=True)
                            suite_name, case_name = extract_test_suite_and_case(test_name)
                        
                        # Skip if this looks like a header, empty, or status text
                        if not test_name or test_name.lower() in ['test', 'status', 'view job', 'failed', 'collapse']:
                            continue
                        
                        # Get job name from column 3 (col 0=collapse, col 1=status, col 2=test, col 3=job)
                        job_name = ""
                        if len(cells) >= 4:
                            job_cell = cells[3]
                            job_link = job_cell.find('a')
                            if job_link:
                                job_name = job_link.get_text(strip=True)
                            else:
                                job_name = job_cell.get_text(strip=True)
                        
                        if test_name:
                            # If we didn't extract suite/case above, do it now
                            if not (test_suite_span and test_name_link):
                                suite_name, case_name = extract_test_suite_and_case(test_name)
                            
                            # Try to get error message from next row or expanded content
                            error_msg = ""
                            next_row = row.find_next_sibling('tr')
                            if next_row:
                                # Check if this contains error details (not another test)
                                next_cells = next_row.find_all('td')
                                if len(next_cells) <= 2:  # Error rows typically have fewer cells
                                    error_text = next_row.get_text(strip=True)
                                    if any(kw in error_text for kw in ['Error', 'Failure', 'Assert', 'RFC', 'MUST', 'Exception']):
                                        error_msg = error_text
                            
                            results['new_failures'].append({
                                'test': test_name,
                                'suite': suite_name,
                                'case': case_name,
                                'job': job_name,
                                'error': error_msg
                            })
    
    # Parse fixed tests and existing failures
    # Search for section headers
    section_headings = ['Fixed tests', 'Existing test failures']
    
    for heading_text in section_headings:
        heading = soup.find(string=lambda x: x and heading_text in x)
        if heading:
            # The heading might be inside a caption within the table, or a separate heading before the table
            # Try to find the parent table first (if heading is in a caption)
            table = heading.find_parent('table')
            if not table and hasattr(heading, 'find_next'):
                # Otherwise find the next table
                table = heading.find_next('table')
            
            if table:
                # Make sure this is a test table, not an artifacts table
                header_row = table.find('tr')
                if header_row:
                    header = header_row.get_text().lower()
                    # Skip if this looks like an artifacts table
                    if 'artifact' in header or 'file size' in header:
                        continue
                    
                    # This should be a test table
                    if 'test' in header or 'status' in header:
                        rows = table.find_all('tr')[1:]  # Skip header
                        for row in rows:
                            cells = row.find_all('td')
                            if len(cells) >= 3:
                                # Existing failures table: col 0=twixie, col 1=status, col 2=test, col 3=failing-since, col 4=job
                                # Fixed tests table: col 0=status, col 1=test, col 2=failing-since, col 3=job
                                # New failures table: col 0=twixie, col 1=status, col 2=test, col 3=job
                                
                                # Try to extract test info from the standard Bamboo structure
                                test_cell = cells[2] if len(cells) >= 3 else cells[1]
                                test_suite_span = test_cell.find('span', class_='test-class')
                                test_name_link = test_cell.find('a', class_='test-name')
                                
                                if test_suite_span and test_name_link:
                                    suite_name = test_suite_span.get_text(strip=True)
                                    case_name = test_name_link.get_text(strip=True)
                                    test_name = f"{suite_name} [{case_name}]"
                                elif test_name_link:
                                    case_name = test_name_link.get_text(strip=True)
                                    suite_name = None
                                    test_name = case_name
                                else:
                                    test_name = test_cell.get_text(strip=True)
                                    suite_name, case_name = extract_test_suite_and_case(test_name)
                                
                                # Skip empty or header-like entries
                                if not test_name or test_name.lower() in ['test', 'status', 'failed', 'expand']:
                                    continue
                                
                                # Get job name - for existing failures it's in column 4
                                job_name = ""
                                job_col = 4 if len(cells) >= 6 else 3  # 6 cols = existing failures, else new/fixed
                                if len(cells) > job_col:
                                    job_cell = cells[job_col]
                                    job_link = job_cell.find('a')
                                    if job_link:
                                        job_name = job_link.get_text(strip=True)
                                    else:
                                        job_name = job_cell.get_text(strip=True)
                                
                                if heading_text == 'Fixed tests':
                                    # Check status to confirm it's fixed
                                    status_text = cells[0].get_text(strip=True).lower() if len(cells) > 0 else ""
                                    if 'success' in status_text or 'expand' not in status_text:
                                        results['fixed_tests'].append(test_name)
                                elif heading_text == 'Existing test failures':
                                    results['existing_failures'].append({
                                        'test': test_name,
                                        'suite': suite_name,
                                        'case': case_name,
                                        'job': job_name
                                    })
    
    # Parse job failures - look for jobs with Failed or Unknown status
    job_list_items = soup.find_all('li', id=re.compile(r'^job-'))
    for job_item in job_list_items:
        job_status = job_item.get('class', [''])[0]
        if job_status in ['Failed', 'Unknown']:
            job_title = job_item.get('title', '')
            job_key = job_item.get('data-job-key', '')
            
            # Try to find hung build message or other error info
            reason = ""
            if job_status == 'Unknown':
                # Look for hung build comments
                # Search within the page for comments related to this job
                hung_msg = soup.find(string=re.compile(r'Detected hung build state'))
                if hung_msg:
                    reason = "Hung build detected (logs quiet for extended period)"
                else:
                    reason = "Unknown status"
            else:
                reason = "Job failed"
            
            results['failed_jobs'].append({
                'name': job_title,
                'status': job_status,
                'reason': reason,
                'key': job_key
            })
    
    # Final status check: If we found test failures or job failures but status is still unknown, mark as failed
    if results['status'] == 'Unknown' and (results['new_failures'] or results['existing_failures'] or results['failed_jobs']):
        results['status'] = 'FAILED'
    
    # If status is still unknown and we have test count but no failures, assume success
    if results['status'] == 'Unknown' and results['total_tests'] > 0 and not results['new_failures'] and not results['existing_failures'] and not results['failed_jobs']:
        results['status'] = 'SUCCESS'
    
    return results


def print_results(results):
    """Print the results in a readable format."""
    print("=" * 80)
    print(f"CI Build Analysis")
    print("=" * 80)
    print(f"URL:          {results['url']}")
    if results['build_number']:
        print(f"Build:        {results['build_number']}")
    print(f"Status:       {results['status']}")
    if results['completed_time']:
        print(f"Completed:    {results['completed_time']}")
    
    if results['total_tests'] > 0:
        print(f"Total Tests:  {results['total_tests']}")
    if results['quarantined_skipped'] > 0:
        print(f"Quarantined/Skipped: {results['quarantined_skipped']}")
    
    print("=" * 80)
    
    if results['status'] == 'SUCCESS':
        print("\n✓ Build PASSED - No failures detected!")
        if results['fixed_tests']:
            print(f"\n✓ Fixed {len(results['fixed_tests'])} test(s):")
            for test in results['fixed_tests']:
                print(f"  - {test}")
    else:
        print("\n✗ Build FAILED")
        
        # Print failed jobs first if any
        if results['failed_jobs']:
            print(f"\n{'='*80}")
            print(f"FAILED/HUNG JOBS:")
            print(f"{'='*80}")
            for job in results['failed_jobs']:
                print(f"  ✗ {job['name']}")
                print(f"     Status: {job['status']}")
                print(f"     Reason: {job['reason']}")
            print(f"{'='*80}")
        
        # Print summary of failing test cases (both new and existing)
        all_failures = results['new_failures'] + results['existing_failures']
        if all_failures:
            print(f"\n{'='*80}")
            print(f"FAILING TEST CASES:")
            print(f"{'='*80}")
            for failure in all_failures:
                print(f"  ✗ {failure['case']}")
            print(f"{'='*80}")
        
        # Print detailed information
        if results['new_failures']:
            print(f"\n✗ NEW TEST FAILURES - DETAILED INFORMATION ({len(results['new_failures'])}):")
            print("-" * 80)
            for i, failure in enumerate(results['new_failures'], 1):
                print(f"\n{i}. Test Case:  {failure['case']}")
                if failure['suite']:
                    print(f"   Suite:      {failure['suite']}")
                print(f"   Job:        {failure['job']}")
                if failure['error']:
                    # Clean up the error message
                    error_lines = failure['error'].split('\n')
                    # Take first few lines that have meaningful content
                    meaningful_lines = []
                    for line in error_lines[:10]:  # Limit to first 10 lines
                        line = line.strip()
                        if line and line not in ['Collapse', 'Failed']:
                            meaningful_lines.append(line)
                    
                    if meaningful_lines:
                        print(f"   Error:")
                        for line in meaningful_lines[:5]:  # Show up to 5 lines
                            # Truncate very long lines
                            if len(line) > 100:
                                line = line[:100] + "..."
                            print(f"      {line}")
                        if len(meaningful_lines) > 5:
                            print(f"      ... ({len(meaningful_lines) - 5} more lines)")
        
        if results['existing_failures']:
            print(f"\n✗ EXISTING TEST FAILURES ({len(results['existing_failures'])}):")
            print("-" * 80)
            for i, failure in enumerate(results['existing_failures'], 1):
                case_info = failure['case']
                if failure['suite']:
                    case_info = f"{failure['suite']} [{failure['case']}]"
                print(f"{i}. {case_info}")
                print(f"   Job: {failure['job']}")
        
        if results['fixed_tests']:
            print(f"\n✓ FIXED TESTS ({len(results['fixed_tests'])}):")
            print("-" * 80)
            for test in results['fixed_tests']:
                print(f"  - {test}")
    
    print("\n" + "=" * 80)


def main():
    """Main function."""
    if len(sys.argv) != 2:
        print("Usage: ./check_ci_build.py <build_url>")
        print("Example: ./check_ci_build.py https://ci1.netdef.org/browse/FRR-FRR-9082")
        sys.exit(1)
    
    url = sys.argv[1]
    
    print(f"Downloading: {url}")
    html_content = download_page(url)
    
    print("Parsing build results...")
    results = parse_build_status(html_content, url)
    
    print_results(results)


if __name__ == "__main__":
    main()

