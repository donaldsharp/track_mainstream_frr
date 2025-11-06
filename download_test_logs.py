#!/usr/bin/env python3
"""
Download test logs from a Bamboo CI build.

This script downloads the TopotestLog artifacts from all Basic Tests jobs
in a specified Bamboo build.

Usage: ./download_test_logs.py [options] <build_url>
Example: ./download_test_logs.py https://ci1.netdef.org/browse/FRR-PULLREQ3-U18I386BUILD-12091
"""

import sys
import os
import re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup


def download_file(url, output_path):
    """Download a file from URL to output_path."""
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Download the file
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        return True
    except Exception as e:
        print(f"  ✗ Error downloading {url}: {e}")
        return False


def download_artifacts_recursive(artifact_url, output_dir, indent=2):
    """Recursively download artifacts from a directory."""
    downloaded_count = 0
    
    try:
        response = requests.get(artifact_url, timeout=30)
        if response.status_code == 404:
            return 0
        response.raise_for_status()
    except Exception as e:
        print(f"{' '*indent}✗ Error accessing {artifact_url}: {e}")
        return 0
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Find all table rows in the artifact listing
    # Bamboo uses a table with specific icon classes to distinguish files from directories
    rows = soup.find_all('tr')
    
    for row in rows:
        # Find the link in this row
        link = row.find('a', href=True)
        if not link:
            continue
        
        href = link['href']
        text = link.get_text(strip=True)
        
        # Skip parent directory and empty links
        if not text or text in ['..', '/', 'Parent directory', '../', 'Parent Directory']:
            continue
        
        if href in ['../', '../', '/']:
            continue
        
        # Check if this is a directory by looking at the icon class
        # Bamboo uses different icon classes for directories and files
        icon = row.find('span', class_='aui-icon')
        is_directory = False
        
        if icon:
            icon_classes = icon.get('class', [])
            # Directory icons have 'aui-iconfont-folder-filled' class
            # File icons have 'aui-iconfont-file' class
            if 'aui-iconfont-folder-filled' in icon_classes or 'aui-iconfont-folder' in icon_classes:
                is_directory = True
        
        # Construct full URL
        if href.startswith('http'):
            full_url = href
        elif href.startswith('/artifact/'):
            # Bamboo artifact URLs start with /artifact/
            full_url = f"https://ci1.netdef.org{href}"
        else:
            full_url = urljoin(artifact_url, href)
        
        if is_directory:
            # Create subdirectory and recurse
            subdir_name = text.rstrip('/')
            subdir_path = os.path.join(output_dir, subdir_name)
            os.makedirs(subdir_path, exist_ok=True)
            print(f"{' '*indent}📁 Entering directory: {subdir_name}")
            downloaded_count += download_artifacts_recursive(full_url, subdir_path, indent + 2)
        else:
            # Download file
            output_path = os.path.join(output_dir, text)
            print(f"{' '*indent}Downloading: {text}...", end=' ')
            if download_file(full_url, output_path):
                print("✓")
                downloaded_count += 1
            else:
                print("✗")
    
    return downloaded_count


def extract_job_short_name(job_key):
    """Extract the short job name from the full job key.
    
    Example: FRR-PULLREQ3-TOPO0D12ARM8-12091 -> TOPO0D12ARM8
    """
    # Remove the build number (last part after last dash)
    parts = job_key.rsplit('-', 1)
    if len(parts) > 1:
        job_key_no_build = parts[0]
        # Remove the plan key prefix (everything up to the last dash component that's part of plan)
        # FRR-PULLREQ3-TOPO0D12ARM8 -> TOPO0D12ARM8
        job_parts = job_key_no_build.split('-')
        if len(job_parts) > 2:
            # Typically plan is first 2-3 parts, job is the rest
            # Try to find where the plan ends - usually after PULLREQ3 or similar
            for i in range(len(job_parts)-1, 0, -1):
                potential_job = '-'.join(job_parts[i:])
                if len(potential_job) > 4:  # Job names are usually longer
                    return potential_job
        return job_parts[-1]  # Fallback to last part
    return job_key


def extract_plan_key(build_key):
    """Extract the plan key from the full build key.
    
    Example: FRR-PULLREQ3-12091 -> FRR-PULLREQ3
    Example: FRR-PULLREQ3-TOPO0D12ARM8-12091 -> FRR-PULLREQ3
    """
    # Remove the build number (last part)
    parts = build_key.rsplit('-', 1)
    if len(parts) > 1:
        without_build = parts[0]
        # If it contains a job name, remove that too
        # Check if this looks like it has a job (more than 2 dashes typically)
        if without_build.count('-') > 1:
            # Try to extract just the plan part (typically first 2 segments)
            plan_parts = without_build.split('-')
            if len(plan_parts) >= 2:
                return '-'.join(plan_parts[:2])
        return without_build
    return build_key


def download_job_artifacts(build_key, job_key, job_name, output_dir):
    """Download TopotestLog-related artifacts for a specific job."""
    print(f"\n{'='*80}")
    print(f"Job: {job_name}")
    print(f"Key: {job_key}")
    print(f"{'='*80}")
    
    # Extract plan key and build number
    plan_key = extract_plan_key(build_key)
    build_number = build_key.rsplit('-', 1)[-1]
    
    # Extract job short name
    job_short_name = extract_job_short_name(job_key)
    
    print(f"Plan: {plan_key}, Build: {build_number}, Job: {job_short_name}")
    
    # First, access the job's artifact page to see what artifacts are available
    job_artifact_list_url = f"https://ci1.netdef.org/browse/{job_key}/artifact"
    
    print(f"Checking artifacts at: {job_artifact_list_url}")
    
    try:
        response = requests.get(job_artifact_list_url, timeout=30)
        if response.status_code == 404:
            print("  ℹ No artifacts page found for this job")
            return 0
        response.raise_for_status()
    except Exception as e:
        print(f"  ✗ Error accessing artifacts page: {e}")
        return 0
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Look for TopotestDetails and TopotestLogs artifacts
    artifacts_to_download = []
    for link in soup.find_all('a', href=True):
        href = link['href']
        text = link.get_text(strip=True)
        
        # Check for Topotest-related artifacts
        if 'Topotest' in text and '/artifact/' in href:
            artifacts_to_download.append({
                'name': text,
                'url': f"https://ci1.netdef.org{href}" if href.startswith('/') else href
            })
    
    if not artifacts_to_download:
        print("  ℹ No Topotest artifacts found for this job")
        return 0
    
    print(f"  Found {len(artifacts_to_download)} Topotest artifact(s)")
    
    # Create output directory for this job
    safe_job_name = re.sub(r'[^\w\s-]', '', job_name).strip()
    safe_job_name = re.sub(r'[-\s]+', '_', safe_job_name)
    job_output_dir = os.path.join(output_dir, safe_job_name)
    os.makedirs(job_output_dir, exist_ok=True)
    
    # Download each artifact
    total_downloaded = 0
    for artifact in artifacts_to_download:
        print(f"\n  Downloading artifact: {artifact['name']}")
        artifact_output_dir = os.path.join(job_output_dir, artifact['name'])
        os.makedirs(artifact_output_dir, exist_ok=True)
        
        downloaded = download_artifacts_recursive(artifact['url'], artifact_output_dir, indent=4)
        total_downloaded += downloaded
        
        if downloaded > 0:
            print(f"    ✓ Downloaded {downloaded} file(s) from {artifact['name']}")
    
    if total_downloaded == 0:
        print("  ℹ No files downloaded")
    else:
        print(f"\n  ✓ Total: {total_downloaded} file(s) downloaded")
    
    return total_downloaded


def parse_build_page(build_url):
    """Parse the build page to find all Basic Tests jobs."""
    try:
        response = requests.get(build_url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract the base URL and build key from the URL
        base_url = 'https://ci1.netdef.org'
        build_key = build_url.rsplit('/', 1)[-1]  # Get build key
        
        # Find the "Basic Tests" stage
        basic_tests_jobs = []
        
        # Parse the page structure - look for job links in the artifact listing
        # Jobs are typically listed as links with format /browse/JOB-KEY/artifact
        
        # First, try to find structured stage/job elements
        stage_sections = soup.find_all(['div', 'section', 'li'], class_=lambda x: x and 'stage' in str(x).lower() if x else False)
        
        for section in stage_sections:
            section_text = section.get_text()
            if 'Basic Tests' in section_text or 'Basic Test' in section_text:
                # Found the Basic Tests stage, now find jobs within it
                job_links = section.find_all('a', href=re.compile(r'/browse/'))
                for link in job_links:
                    job_name = link.get_text(strip=True)
                    job_url = link['href']
                    
                    if job_name and '/browse/' in job_url:
                        # Construct full URL if needed
                        if not job_url.startswith('http'):
                            job_url = urljoin(base_url, job_url)
                        
                        # Extract job key from URL
                        job_key = job_url.rsplit('/', 1)[-1]
                        
                        # Skip if it's the parent build link
                        if job_key == build_key:
                            continue
                        
                        basic_tests_jobs.append({
                            'name': job_name,
                            'key': job_key,
                            'url': job_url
                        })
        
        # Alternative approach: look for all job links and filter by known patterns
        if not basic_tests_jobs:
            print("  Using pattern-based job detection...")
            known_patterns = [
                'AddressSanitizer',
                'TopoTests',
                'IPv4 LDP Protocol',
                'IPv4 Protocols',
                'IPv6 Protocols',
                'Static Analyzer',
                'Deb Pkg Check'
            ]
            
            seen_keys = set()
            for link in soup.find_all('a', href=True):
                job_text = link.get_text(strip=True)
                href = link['href']
                
                # Check if this looks like a Basic Tests job
                if any(pattern in job_text for pattern in known_patterns):
                    if '/browse/' in href and job_text:
                        # Construct full URL
                        if not href.startswith('http'):
                            job_url = urljoin(base_url, href)
                        else:
                            job_url = href
                        
                        # Extract job key
                        job_key = job_url.rsplit('/', 1)[-1].replace('/artifact', '')
                        
                        # Avoid duplicates
                        if job_key not in seen_keys and job_key != build_key:
                            seen_keys.add(job_key)
                            basic_tests_jobs.append({
                                'name': job_text,
                                'key': job_key,
                                'url': job_url
                            })
        
        # Remove duplicates based on key
        unique_jobs = []
        seen_keys = set()
        for job in basic_tests_jobs:
            if job['key'] not in seen_keys:
                seen_keys.add(job['key'])
                unique_jobs.append(job)
        
        return build_key, unique_jobs
        
    except Exception as e:
        print(f"Error parsing build page: {e}")
        import traceback
        traceback.print_exc()
        return None, []


def main():
    """Main function."""
    # Parse command line arguments
    list_only = False
    build_url = None
    chunk_url = None
    
    i = 0
    while i < len(sys.argv[1:]):
        arg = sys.argv[i + 1]
        
        if arg in ['--list-jobs', '--list', '-l']:
            list_only = True
        elif arg in ['--chunk', '-c']:
            # Next argument should be the chunk URL
            if i + 1 < len(sys.argv[1:]):
                chunk_url = sys.argv[i + 2]
                i += 1  # Skip next argument
            else:
                print("Error: --chunk requires a URL argument")
                sys.exit(1)
        elif arg.startswith('http'):
            build_url = arg
        elif arg in ['--help', '-h']:
            print("Usage: ./download_test_logs.py [options] <build_url>")
            print()
            print("Options:")
            print("  --list-jobs, -l           List jobs without downloading")
            print("  --chunk <url>, -c <url>   Download a specific job by its artifact URL")
            print("  --help, -h                Show this help message")
            print()
            print("Examples:")
            print("  # Download all Basic Tests from a build")
            print("  ./download_test_logs.py https://ci1.netdef.org/browse/FRR-PULLREQ3-12091")
            print()
            print("  # List all jobs without downloading")
            print("  ./download_test_logs.py --list-jobs https://ci1.netdef.org/browse/FRR-PULLREQ3-12091")
            print()
            print("  # Download a specific job")
            print("  ./download_test_logs.py --chunk https://ci1.netdef.org/browse/FRR-PULLREQ3-ASAN6D12AMD64-12091/artifact")
            sys.exit(0)
        
        i += 1
    
    # Handle chunk mode
    if chunk_url:
        if not chunk_url.startswith('http'):
            print("Error: Invalid chunk URL")
            sys.exit(1)
        
        # Extract job key from chunk URL
        # Format: https://ci1.netdef.org/browse/JOB-KEY/artifact
        if '/browse/' not in chunk_url:
            print("Error: Invalid Bamboo artifact URL")
            print("Expected format: https://ci1.netdef.org/browse/JOB-KEY/artifact")
            sys.exit(1)
        
        # Extract job key
        parts = chunk_url.split('/browse/')
        if len(parts) < 2:
            print("Error: Could not extract job key from URL")
            sys.exit(1)
        
        job_key = parts[1].rstrip('/').replace('/artifact', '')
        
        # Extract build key from job key (remove job suffix to get build)
        build_key = job_key.rsplit('-', 1)[0]  # Remove build number
        if '-' in build_key:
            # Try to extract just the plan and build number
            # FRR-PULLREQ3-ASAN6D12AMD64-12091 -> FRR-PULLREQ3-12091
            parts = job_key.split('-')
            if len(parts) >= 3:
                # Last part is build number
                build_number = parts[-1]
                # First two parts are typically the plan
                plan_parts = parts[:2]
                build_key = '-'.join(plan_parts + [build_number])
        
        print(f"{'='*80}")
        print(f"Downloading Single Job Artifacts (--chunk mode)")
        print(f"{'='*80}")
        print(f"Job URL: {chunk_url}")
        print(f"Job Key: {job_key}")
        print(f"Build Key: {build_key}")
        
        # Create output directory
        output_dir = os.path.join(os.getcwd(), f"logs_{job_key}")
        os.makedirs(output_dir, exist_ok=True)
        
        print(f"Output directory: {output_dir}")
        
        # Download artifacts for this specific job
        # We need to determine the job name from the key
        # For now, use the job key as the name
        job_name = job_key.replace('-', ' ')
        
        files_downloaded = download_job_artifacts(
            build_key,
            job_key,
            job_name,
            output_dir
        )
        
        # Summary
        print(f"\n{'='*80}")
        print("DOWNLOAD SUMMARY")
        print(f"{'='*80}")
        print(f"Job processed:               {job_key}")
        print(f"Total files downloaded:      {files_downloaded}")
        print(f"Output directory:            {output_dir}")
        print(f"{'='*80}")
        
        if files_downloaded > 0:
            print("\n✓ Download complete!")
        else:
            print("\n⚠ No files were downloaded.")
        
        return
    
    if not build_url:
        print("Error: No build URL provided")
        print()
        print("Usage: ./download_test_logs.py [options] <build_url>")
        print("Example: ./download_test_logs.py https://ci1.netdef.org/browse/FRR-PULLREQ3-12091")
        print()
        print("Use --help for more options")
        sys.exit(1)
    
    # Validate URL
    if 'ci1.netdef.org/browse/' not in build_url:
        print("Error: Invalid Bamboo build URL")
        print("Expected format: https://ci1.netdef.org/browse/BUILD-KEY")
        sys.exit(1)
    
    print(f"{'='*80}")
    print(f"Downloading Test Logs from Bamboo Build")
    print(f"{'='*80}")
    print(f"Build URL: {build_url}")
    
    # Parse the build page to find jobs
    print("\nParsing build page to find Basic Tests jobs...")
    build_key, jobs = parse_build_page(build_url)
    
    if not build_key:
        print("\n✗ Failed to parse build page")
        sys.exit(1)
    
    if not jobs:
        print("\n✗ No Basic Tests jobs found on the build page")
        print("  This could mean:")
        print("    - The build page structure is different than expected")
        print("    - There are no Basic Tests jobs in this build")
        print("    - The page requires authentication")
        sys.exit(1)
    
    print(f"\nFound {len(jobs)} Basic Tests job(s)")
    
    # If list-only mode, just show the jobs and exit
    if list_only:
        print(f"\n{'='*80}")
        print("JOBS FOUND (list-only mode - not downloading)")
        print(f"{'='*80}")
        for i, job in enumerate(jobs, 1):
            print(f"{i:3d}. {job['name']}")
            print(f"      Key: {job['key']}")
        print(f"{'='*80}")
        print(f"\nTotal: {len(jobs)} job(s)")
        print("\nTo download artifacts, run without --list-jobs flag")
        return
    
    # Create output directory
    # Extract build identifier from URL for directory name
    build_id = build_key
    output_dir = os.path.join(os.getcwd(), f"logs_{build_id}")
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Output directory: {output_dir}")
    
    # Download artifacts for each job
    total_files = 0
    successful_jobs = 0
    
    for job in jobs:
        files_downloaded = download_job_artifacts(
            build_key,
            job['key'],
            job['name'],
            output_dir
        )
        
        total_files += files_downloaded
        if files_downloaded > 0:
            successful_jobs += 1
    
    # Summary
    print(f"\n{'='*80}")
    print("DOWNLOAD SUMMARY")
    print(f"{'='*80}")
    print(f"Total jobs processed:        {len(jobs)}")
    print(f"Jobs with artifacts:         {successful_jobs}")
    print(f"Total files downloaded:      {total_files}")
    print(f"Output directory:            {output_dir}")
    print(f"{'='*80}")
    
    if total_files > 0:
        print("\n✓ Download complete!")
    else:
        print("\n⚠ No files were downloaded. This could mean:")
        print("  - The jobs don't have TopotestLog artifacts")
        print("  - The artifact directory structure is different than expected")
        print("  - Authentication might be required")


if __name__ == "__main__":
    main()
