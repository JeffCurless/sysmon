#!/usr/bin/env bash
# Build the sysmon Debian package.
#
# Usage:  ./build_package.sh [--skip-tests] [--install]
#
#   --skip-tests  don't run ./run_tests.sh before building
#   --install     after a successful build, (re)install the package with apt
#                 (needs sudo)
#
# Steps: check build tools -> run tests/lint -> dpkg-buildpackage -> lintian.
# The .deb, .buildinfo and .changes files end up in dist/ (dpkg-buildpackage
# writes them to the parent directory; they are moved from there).
# The version comes from debian/changelog.

set -euo pipefail

usage() {
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
}

run_tests=1
install=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) run_tests=0 ;;
        --install)    install=1 ;;
        -h|--help)    usage; exit 0 ;;
        *) echo "unknown option: $arg" >&2; usage >&2; exit 2 ;;
    esac
done

# Run from the repo root (dpkg-buildpackage needs debian/ in the cwd).
cd "$(dirname "$(readlink -f "$0")")"

step() { printf '\n==== %s ====\n' "$1"; }
die()  { echo "build_package.sh: $*" >&2; exit 1; }

step "Checking build tools"
command -v dpkg-buildpackage >/dev/null || die "dpkg-buildpackage not found (sudo apt install dpkg-dev)"
command -v dpkg-parsechangelog >/dev/null || die "dpkg-parsechangelog not found (sudo apt install dpkg-dev)"
dpkg -s debhelper >/dev/null 2>&1 || die "debhelper is not installed (sudo apt install debhelper)"

package=$(dpkg-parsechangelog -S Source)
version=$(dpkg-parsechangelog -S Version)
echo "Building $package $version"

# The PNG icons are committed, not generated during the build; make sure none is missing.
for size in 16 22 24 32 48 64 128 256; do
    [ -f "icons/sysmon${size}x${size}.png" ] || die "icons/sysmon${size}x${size}.png is missing; run: python3 icons/make_icons.py"
done

if [ "$run_tests" -eq 1 ]; then
    step "Running tests, pylint and mypy"
    ./run_tests.sh || die "checks failed; fix them or rerun with --skip-tests"
fi

step "Building package"
dpkg-buildpackage -us -uc -b

parent=$(dirname "$PWD")
deb="${package}_${version}_all.deb"
[ -f "$parent/$deb" ] || die "expected $parent/$deb was not produced"

mkdir -p dist
mv -f "$parent/$deb" dist/
for f in "$parent/${package}_${version}"_*.buildinfo "$parent/${package}_${version}"_*.changes; do
    [ -e "$f" ] && mv -f "$f" dist/
done

# Remove the build tree (debian/sysmon/, stamps, ...) so the repo is left clean.
debian/rules clean >/dev/null

step "lintian"
if command -v lintian >/dev/null; then
    lintian "dist/$deb" || die "lintian reported problems"
    echo "lintian: clean"
else
    echo "lintian is not installed; skipped (sudo apt install lintian)"
fi

if [ "$install" -eq 1 ]; then
    step "Installing"
    sudo apt-get install -y --reinstall "./dist/$deb"
fi

step "Done"
echo "Package: dist/$deb"
[ "$install" -eq 1 ] || echo "Install with: sudo apt install ./dist/$deb"
