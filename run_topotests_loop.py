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

    # Parse known args to get our script's arguments
    args, pytest_args = parser.parse_known_args()

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
                stress_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
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
                else "single-threaded"
                if args.parallel == 1
                else f"{args.parallel} workers"
            )
            log_file.write(f"# Parallel: {parallel_desc}\n")
        if args.stress is not None:
            log_file.write(f"# Stress: {args.stress} CPU workers\n")
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
            else "single-threaded"
            if args.parallel == 1
            else f"{args.parallel} workers"
        )
        print(f"Parallel: {parallel_desc}")
    if args.stress is not None:
        print(f"Stress: {args.stress} CPU workers (PID {stress_process.pid})")
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

        stop_stress_process(stress_process)
        sys.exit(130)

    except Exception as e:
        print(f"\nUnexpected error: {e}")
        if args.log_file:
            log_file.write(f"\nUnexpected error: {e}\n")
            log_file.close()
        stop_stress_process(stress_process)
        sys.exit(1)


if __name__ == "__main__":
    main()
