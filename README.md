# FRR CI Build Checker

Python scripts to download and analyze FRR CI build pages from ci1.netdef.org to identify test failures and track trends.

## Scripts

1. **`check_ci_build.py`** - Analyze a single CI build
2. **`analyze_ci.py`** - Analyze multiple builds and generate statistics
3. **`download_test_logs.py`** - Download test log artifacts from CI builds

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

Or install directly:
```bash
pip install requests beautifulsoup4
```

## Usage

### Single Build Analysis

Run `check_ci_build.py` with a Bamboo CI build URL:

```bash
./check_ci_build.py <build_url>
```

### Examples

Check a successful build:
```bash
./check_ci_build.py https://ci1.netdef.org/browse/FRR-FRR-9083
```

Check a failed build:
```bash
./check_ci_build.py https://ci1.netdef.org/browse/FRR-FRR-9082
```

```bash
./check_ci_build.py https://ci1.netdef.org/browse/FRR-FRR-9081
```

### Multi-Build Analysis

Run `analyze_ci.py` with a build number and optional time period. The script will analyze builds from N days **before the specified build's completion time**, making it useful for historical analysis.

```bash
./analyze_ci.py <build_number> [days]
```

#### Examples

Analyze 7 days before build 9083 (default):
```bash
./analyze_ci.py 9083
```

Analyze 14 days before build 9083's completion:
```bash
./analyze_ci.py 9083 14
```

Analyze 7 days before build 9059's completion:
```bash
./analyze_ci.py 9059 7
```

### Download Test Logs

Run `download_test_logs.py` with a Bamboo CI build URL to download TopotestLog artifacts from all Basic Tests jobs:

```bash
./download_test_logs.py [options] <build_url>
```

#### Options

- `--list-jobs, -l`: List jobs without downloading
- `--chunk <url>, -c <url>`: Download a specific job by its artifact URL
- `--help, -h`: Show help message

#### Examples

Download all test logs from a build:
```bash
./download_test_logs.py https://ci1.netdef.org/browse/FRR-PULLREQ3-U18I386BUILD-12091
```

List available jobs without downloading:
```bash
./download_test_logs.py --list-jobs https://ci1.netdef.org/browse/FRR-PULLREQ3-U18I386BUILD-12091
```

Download a specific job only:
```bash
./download_test_logs.py --chunk https://ci1.netdef.org/browse/FRR-PULLREQ3-ASAN6D12AMD64-12091/artifact
```

The download_test_logs.py script will:
- Parse the build page to find all Basic Tests jobs
- For each job, locate the TopotestLog artifacts directory
- Download all log files recursively, preserving directory structure
- Organize downloads into a `logs_BUILD-KEY` directory with subdirectories for each job
- Display progress and summary of downloaded files

The analyze_ci.py script will:
- Fetch the specified build to determine its completion date
- Scan backwards from that build
- Analyze all builds within the specified time period before the reference build (default: 7 days)
- Group failures by type
- Generate statistics on:
  - Success/failure rates
  - Most common test failures (combined with job context)
  - Most common job failures  
  - Hung/timeout issues
  - Failure patterns across builds

## Output

### Single Build Output

The script will display:
- Build number and status (PASSED/FAILED)
- Total number of tests
- New test failures (with test name, job, and error details)
- Existing test failures
- Fixed tests
- Quarantined/skipped tests

### Example Output for Failed Build

```
================================================================================
CI Build Analysis
================================================================================
URL:          https://ci1.netdef.org/browse/FRR-FRR-9081
Build:        #9081
Status:       FAILED
Total Tests:  21832
Quarantined/Skipped: 797
================================================================================

✗ Build FAILED

================================================================================
FAILING TEST CASES:
================================================================================
  ✗ ANVL-LDP-9.5
================================================================================

✗ NEW TEST FAILURES - DETAILED INFORMATION (1):
--------------------------------------------------------------------------------

1. Test Case:  ANVL-LDP-9.5
   Suite:      RFC-Compliance-tests
   Job:        IPv4 LDP Protocol on Debian 12
   Error:
      RFC Failure: MUST Peer 192.168.0.101 did not forward MPLS packet with label 17
      ============== Reference: ============================
      RFC 3036, s2.7 p23 LDP Identifiers and Next Hop Addresses
      ============== Test Summary: =========================
      Similarly, when the LSR learns a label for a prefix from an LDP peer, it must be able to determine...

================================================================================
```

### Example Output for Successful Build

```
================================================================================
CI Build Analysis
================================================================================
URL:          https://ci1.netdef.org/browse/FRR-FRR-9083
Build:        #9083
Status:       SUCCESS
================================================================================

✓ Build PASSED - No failures detected!

================================================================================
```

### Weekly Analysis Output

```
================================================================================
CI BUILDS ANALYSIS SUMMARY
================================================================================

Total Builds Analyzed: 25
Successful:            18 (72.0%)
Failed:                7 (28.0%)

Success Rate:          72.0%

================================================================================
TOP TEST FAILURES (by frequency)
================================================================================
 1. test_refout                                        -  27 failures (108.0% of builds)
 2. test_pim6_RP_configured_as_FHR_p1                  -   3 failures (12.0% of builds)
 3. ANVL-LDP-9.5                                       -   2 failures (8.0% of builds)

================================================================================
JOB FAILURES
================================================================================
  • Debian 12 amd64 build                              -   5 failures (20.0%)
  • TopoTests Ubuntu 22.04 amd64 Part 5                -   2 failures (8.0%)

================================================================================
HUNG/TIMEOUT JOBS
================================================================================
  • TopoTests Debian 12 arm8 Part 7                    -   3 times (12.0%)
  • AddressSanitizer Debian 12 amd64 Part 0            -   1 times (4.0%)

================================================================================
ERROR TYPES
================================================================================
  • AssertionError                 -  15 occurrences
  • RFC Compliance                 -   2 occurrences
  • Timeout/Hung                   -   3 occurrences

================================================================================
FAILURE PATTERNS (builds with same failures grouped)
================================================================================

Pattern #1: Affects 15 builds
Builds: #9045, #9046, #9047...
Failures:
  ✗ Test: test_refout
```

## Features

### check_ci_build.py Features

- Downloads and parses Bamboo CI build pages
- Identifies build status (passed/failed)
- **Extracts exact test case names** (e.g., `ANVL-LDP-9.5`, `test_rib_ipv6_step3`)
- Separates test suites from test cases for clarity
- Shows completion time for builds
- Detects and reports:
  - New test failures with error details
  - Existing test failures
  - Fixed tests
  - Failed jobs
  - Hung/timeout jobs with detection messages
- Provides detailed information including:
  - Test case name
  - Test suite name
  - Job where the failure occurred
  - Error messages (AssertionError, RFC violations, etc.)

### analyze_ci.py Features

- Analyzes multiple builds over a time period (default: 7 days)
- Calculates success/failure rates across builds
- Groups and ranks failures by frequency:
  - Most common test failures
  - Most common job failures
  - Most common hung/timeout issues
- Categorizes error types (AssertionError, RFC Compliance, Timeout, etc.)
- Identifies failure patterns across multiple builds
- Shows which builds share the same failure signatures
- Provides statistical analysis for trend identification

### download_test_logs.py Features

- Downloads TopotestLog artifacts from all Basic Tests jobs in a CI build
- Automatically discovers and parses the build page structure
- Supports multiple job types:
  - AddressSanitizer tests
  - TopoTests (various platforms and parts)
  - IPv4/IPv6 Protocol tests
  - Static Analyzer results
  - Debian package checks
- Recursively downloads all files from artifact directories
- Preserves directory structure in local filesystem
- Provides real-time download progress with status indicators
- List-only mode to preview jobs before downloading
- Organizes downloads by job name for easy navigation
- Comprehensive error handling and status reporting

