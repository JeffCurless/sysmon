#!/usr/bin/env bash

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Jeff Curless
# See the LICENSE file in the project root for the full license text.

# Run every check for sysmon: the pytest suite, pylint, and mypy.
#
# Usage:  ./run_tests.sh [pytest args...]
#   e.g.  ./run_tests.sh -k scaling -v
#
# Each check runs even if an earlier one fails; the script exits non-zero
# if any check failed.  A tool that isn't installed is reported and counted
# as a failure, so a clean run really means everything was checked.

set -u

# Run from the repo root so the configs (pytest.ini, .pylintrc, mypy.ini) apply.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

# Tests run headless (conftest.py also sets this, but be explicit).
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"

failed=()

run() {
    local name=$1; shift
    printf '\n==== %s ====\n' "$name"
    if "$@"; then
        printf '%s: OK\n' "$name"
    else
        printf '%s: FAILED\n' "$name"
        failed+=("$name")
    fi
}

have_module() {
    python3 -c "import $1" 2>/dev/null
}

if have_module pytest; then
    run pytest python3 -m pytest "$@"
else
    printf '\n==== pytest ====\npytest is not installed (see requirements-dev.txt)\n'
    failed+=(pytest)
fi

if command -v pylint >/dev/null; then
    run pylint pylint src/*.py tests/*.py icons/*.py
else
    printf '\n==== pylint ====\npylint is not installed\n'
    failed+=(pylint)
fi

if command -v mypy >/dev/null; then
    run mypy mypy src/*.py tests/*.py icons/*.py
else
    printf '\n==== mypy ====\nmypy is not installed\n'
    failed+=(mypy)
fi

if ! have_module hypothesis; then
    printf '\nNote: hypothesis is not installed, so the property tests were skipped.\n'
fi

printf '\n==== Summary ====\n'
if [ ${#failed[@]} -eq 0 ]; then
    echo "All checks passed."
    exit 0
fi
echo "Failed: ${failed[*]}"
exit 1
