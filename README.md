# FRR CI Build Checker

This Python script downloads and analyzes FRR CI build pages from ci1.netdef.org to identify test failures.

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

Run the script with a Bamboo CI build URL:

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

## Output

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

## Features

- Downloads and parses Bamboo CI build pages
- Identifies build status (passed/failed)
- **Extracts exact test case names** (e.g., `ANVL-LDP-9.5`, `test_rib_ipv6_step3`)
- Separates test suites from test cases for clarity
- Shows a clear summary of failing test cases at the top
- Provides detailed information including:
  - Test case name
  - Test suite name
  - Job where the failure occurred
  - Error messages and failure details
- Shows newly failed tests, existing failures, and fixed tests
- Works with standard FRR CI build URLs from ci1.netdef.org

