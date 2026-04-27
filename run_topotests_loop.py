#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Script to run topotests in a loop until a test fails
# Usage: python3 tools/run_topotests_loop.py [pytest_args...]

import subprocess
import sys
import os
import time
import argparse
from datetime import datetime


def run_pytest(args, parallel=None):
    """Run pytest with the given arguments and return the exit code"""
    cmd = ["sudo", "-E", "pytest"]

    # Add parallel options if specified
    if parallel is not None:
        if parallel == 0:
            # Use auto-detection (default pytest behavior)
            pass
        elif parallel == 1:
            # Force single-threaded
            cmd.extend(["-n", "0"])
        else:
            # Use specified number of workers
            cmd.extend(["-n", str(parallel)])

    # Always add --dist=loadfile for topotests
    cmd.extend(["--dist=loadfile"])

    cmd.extend(args)
    print(f"Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, cwd="tests/topotests", check=False)
        return result.returncode
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"Error running pytest: {e}")
        return 1


def stop_stress_process(stress_process):
    """Stop the stress process if it's running"""
    if stress_process is not None:
        try:
            print(f"Stopping stress process (PID {stress_process.pid})...")
            stress_process.terminate()
            try:
                stress_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("Stress process didn't terminate, killing it...")
                stress_process.kill()
                stress_process.wait()
            print("Stress process stopped")
        except Exception as e:
            print(f"Error stopping stress process: {e}")


def start_parallel_route_installation(nexthop):
    """Start parallel route installation via vtysh"""
    install_cmd = (
        f"sharp install routes 10.0.0.0 nexthop {nexthop} 1000000 "
        "table 9000 repeat 1000"
    )
    cmd = ["vtysh", "-c", install_cmd]
    print(f"Starting parallel route installation: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"Error starting parallel route installation: {e}")
        return False

    if result.returncode != 0:
        print(
            "Error: parallel route installation command failed with exit code "
            f"{result.returncode}"
        )
        return False

    return True


def stop_parallel_route_installation():
    """Stop parallel route installation via vtysh"""
    cmd = ["vtysh", "-c", "sharp install stop"]
    print("Stopping parallel route installation...")
    try:
        result = subprocess.run(cmd, check=False)
    except Exception as e:
        print(f"Error stopping parallel route installation: {e}")
        return False

    if result.returncode != 0:
        print(
            "Error: parallel route installation stop failed with exit code "
            f"{result.returncode}"
        )
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Run topotests in a loop until a test fails",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all tests until one fails
  python3 tools/run_topotests_loop.py

  # Run specific test until it fails
  python3 tools/run_topotests_loop.py ospf-topo1/

  # Run with specific pytest options
  python3 tools/run_topotests_loop.py -v -s bgp-topo1/

  # Run with 4 parallel workers
  python3 tools/run_topotests_loop.py --parallel 4 ospf-topo1/

  # Run single-threaded (no parallelism)
  python3 tools/run_topotests_loop.py --parallel 1 ospf-topo1/

  # Run with CPU stress testing (4 workers)
  python3 tools/run_topotests_loop.py --stress 4 ospf-topo1/

  # Run with parallel route installation during tests
  python3 tools/run_topotests_loop.py --parallel-route-installation 192.0.2.1 ospf-topo1/

  # Exit immediately on first test failure
  python3 tools/run_topotests_loop.py --exitfirst ospf-topo1/

  # Only run tests marked for a specific daemon (pytest -m)
  python3 tools/run_topotests_loop.py --daemon-to-test bgpd
        """,
    )

    parser.add_argument(
        "--max-runs", type=int, default=0, help="Maximum number of runs (0 = unlimited)"
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between runs in seconds (default: 1.0)",
    )

    parser.add_argument(
        "--log-file", help="Log file to save run information (optional)"
    )

    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help="Number of parallel workers for pytest (0=auto, 1=single-threaded, N=workers). Default: auto-detect",
    )

    parser.add_argument(
        "--stress",
        type=int,
        default=None,
        help="Run 'stress -c X' in the background during tests (X=number of CPU workers)",
    )

    parser.add_argument(
        "--parallel-route-installation",
        metavar="A.B.C.D",
        default=None,
        help=(
            "Run 'vtysh -c \"sharp install routes 10.0.0.0 nexthop A.B.C.D "
            "1000000 table 9000 repeat 1000\"' before tests"
        ),
    )

    parser.add_argument(
        "--exitfirst",
        "-x",
        action="store_true",
        help="Exit instantly on first error or failed test (passes -x to pytest)",
    )

    parser.add_argument(
        "--daemon-to-test",
        metavar="DAEMON",
        default=None,
        help=("Only run topotests that target DAEMON (passes -m DAEMON to pytest)"),
    )

    # Parse known args to get our script's arguments
    args, pytest_args = parser.parse_known_args()

    # Add exitfirst flag to pytest args if requested
    if args.exitfirst:
        pytest_args.insert(0, "-x")

    if args.daemon_to_test:
        pytest_args.insert(0, args.daemon_to_test)
        pytest_args.insert(0, "-m")

    # Check if we're in the right directory
    if not os.path.exists("tests/topotests"):
        print("Error: This script must be run from the FRR root directory")
        sys.exit(1)

    # Start stress process if requested
    stress_process = None
    if args.stress is not None:
        if args.stress <= 0:
            print("Error: --stress value must be a positive integer")
            sys.exit(1)
        try:
            stress_cmd = ["stress", "-c", str(args.stress)]
            print(f"Starting stress process: {' '.join(stress_cmd)}")
            stress_process = subprocess.Popen(
                stress_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            print(f"Stress process started with PID {stress_process.pid}")
        except FileNotFoundError:
            print("Error: 'stress' command not found. Please install stress package.")
            print("  Ubuntu/Debian: sudo apt-get install stress")
            print("  Fedora/RHEL: sudo dnf install stress")
            sys.exit(1)
        except Exception as e:
            print(f"Error starting stress process: {e}")
            sys.exit(1)

    parallel_route_installation_started = False
    if args.parallel_route_installation is not None:
        if not start_parallel_route_installation(args.parallel_route_installation):
            stop_stress_process(stress_process)
            sys.exit(1)
        parallel_route_installation_started = True

    # Setup logging
    if args.log_file:
        log_file = open(args.log_file, "w")
        log_file.write(f"# Topotests loop run started at {datetime.now()}\n")
        log_file.write(f"# Pytest args: {' '.join(pytest_args)}\n")
        log_file.write(
            f"# Max runs: {args.max_runs if args.max_runs > 0 else 'unlimited'}\n"
        )
        log_file.write(f"# Delay between runs: {args.delay}s\n")
        if args.parallel is not None:
            parallel_desc = (
                "auto"
                if args.parallel == 0
                else (
                    "single-threaded"
                    if args.parallel == 1
                    else f"{args.parallel} workers"
                )
            )
            log_file.write(f"# Parallel: {parallel_desc}\n")
        if args.stress is not None:
            log_file.write(f"# Stress: {args.stress} CPU workers\n")
        if args.parallel_route_installation is not None:
            log_file.write(
                "# Parallel route installation nexthop: "
                f"{args.parallel_route_installation}\n"
            )
        if args.exitfirst:
            log_file.write("# Exit on first failure: enabled\n")
        if args.daemon_to_test:
            log_file.write(f"# Daemon marker filter: -m {args.daemon_to_test}\n")
        log_file.write("\n")

    run_count = 0
    start_time = time.time()

    print(f"Starting topotests loop")
    print(f"Pytest args: {' '.join(pytest_args) if pytest_args else '(all tests)'}")
    print(f"Max runs: {args.max_runs if args.max_runs > 0 else 'unlimited'}")
    print(f"Delay between runs: {args.delay}s")
    if args.parallel is not None:
        parallel_desc = (
            "auto"
            if args.parallel == 0
            else "single-threaded" if args.parallel == 1 else f"{args.parallel} workers"
        )
        print(f"Parallel: {parallel_desc}")
    if args.stress is not None:
        print(f"Stress: {args.stress} CPU workers (PID {stress_process.pid})")
    if args.parallel_route_installation is not None:
        print(
            "Parallel route installation nexthop: "
            f"{args.parallel_route_installation}"
        )
    if args.exitfirst:
        print("Exit on first failure: enabled")
    if args.daemon_to_test:
        print(f"Daemon marker filter: -m {args.daemon_to_test}")
    print(f"Log file: {args.log_file if args.log_file else 'none'}")
    print("-" * 60)

    try:
        while True:
            run_count += 1
            run_start = time.time()

            print(
                f"\n[Run {run_count}] Starting at {datetime.now().strftime('%H:%M:%S')}"
            )

            if args.log_file:
                log_file.write(f"Run {run_count}: {datetime.now()}\n")
                log_file.flush()

            # Run pytest
            exit_code = run_pytest(pytest_args, args.parallel)
            run_duration = time.time() - run_start

            print(
                f"[Run {run_count}] Completed in {run_duration:.2f}s with exit code: {exit_code}"
            )

            if args.log_file:
                log_file.write(
                    f"  Duration: {run_duration:.2f}s, Exit code: {exit_code}\n"
                )
                log_file.flush()

            # Check if test failed
            if exit_code != 0:
                total_duration = time.time() - start_time
                print(f"\n{'='*60}")
                print(f"TEST FAILED on run {run_count}!")
                print(f"Total time: {total_duration:.2f}s")
                print(f"Average time per run: {total_duration/run_count:.2f}s")
                print(f"Exit code: {exit_code}")
                print(f"{'='*60}")

                if args.log_file:
                    log_file.write(f"\nFAILED on run {run_count}\n")
                    log_file.write(f"Total time: {total_duration:.2f}s\n")
                    log_file.write(
                        f"Average time per run: {total_duration/run_count:.2f}s\n"
                    )
                    log_file.write(f"Exit code: {exit_code}\n")
                    log_file.close()

                if parallel_route_installation_started:
                    stop_parallel_route_installation()
                stop_stress_process(stress_process)
                sys.exit(exit_code)

            # Check if we've reached max runs
            if args.max_runs > 0 and run_count >= args.max_runs:
                total_duration = time.time() - start_time
                print(f"\n{'='*60}")
                print(f"Reached maximum runs ({args.max_runs}) without failure!")
                print(f"Total time: {total_duration:.2f}s")
                print(f"Average time per run: {total_duration/run_count:.2f}s")
                print(f"{'='*60}")

                if args.log_file:
                    log_file.write(
                        f"\nCompleted {args.max_runs} runs without failure\n"
                    )
                    log_file.write(f"Total time: {total_duration:.2f}s\n")
                    log_file.write(
                        f"Average time per run: {total_duration/run_count:.2f}s\n"
                    )
                    log_file.close()

                if parallel_route_installation_started:
                    stop_parallel_route_installation()
                stop_stress_process(stress_process)
                sys.exit(0)

            # Wait before next run
            if args.delay > 0:
                print(f"Waiting {args.delay}s before next run...")
                time.sleep(args.delay)

    except KeyboardInterrupt:
        total_duration = time.time() - start_time
        print(f"\n\nInterrupted by user after {run_count} runs")
        print(f"Total time: {total_duration:.2f}s")

        if args.log_file:
            log_file.write(f"\nInterrupted by user after {run_count} runs\n")
            log_file.write(f"Total time: {total_duration:.2f}s\n")
            log_file.close()

        if parallel_route_installation_started:
            stop_parallel_route_installation()
        stop_stress_process(stress_process)
        sys.exit(130)

    except Exception as e:
        print(f"\nUnexpected error: {e}")
        if args.log_file:
            log_file.write(f"\nUnexpected error: {e}\n")
            log_file.close()
        if parallel_route_installation_started:
            stop_parallel_route_installation()
        stop_stress_process(stress_process)
        sys.exit(1)


if __name__ == "__main__":
    main()
