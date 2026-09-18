#!/usr/bin/env bash
# Publish this reviewed source tree with the official GitHub CLI.
set -Eeuo pipefail
umask 077

project_dir=$(CDPATH= cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
repository_name=spatial-workbench
expected_owner=''
dry_run=0
usage() {
    cat <<'USAGE'
Usage: bash tools/publish-github.sh [--name REPOSITORY] [--owner LOGIN] [--dry-run]

Creates a PUBLIC GitHub repository under the authenticated personal account,
commits the reviewed source files, and pushes the current branch. The default
name is spatial-workbench. --owner checks the signed-in account; it does not
select another person or organization. Existing remotes/repositories are refused.

On Arch/CachyOS, missing git/github-cli packages are installed with normal
sudo/pacman prompts. GitHub authentication uses gh's standard browser flow.
--dry-run only checks and lists source files; it makes no changes or requests.
USAGE
}
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
while (( $# )); do
    case "$1" in
        --name|--owner)
            (( $# >= 2 )) || fail "$1 needs a value."
            if [[ "$1" == --name ]]; then repository_name=$2; else expected_owner=$2; fi
            shift 2 ;;
        --dry-run) dry_run=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $1" ;;
    esac
done
[[ "$repository_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] || fail 'Invalid repository name.'
[[ -z "$expected_owner" || "$expected_owner" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}$ ]] || fail 'Invalid account login.'
cd -- "$project_dir"

# Exact reviewed paths: never stage a directory, wildcard, diagnostic report,
# build output, session/token file, or everything with `git add .`.
mapfile -t source_files <<'SOURCE_FILES'
.github/ISSUE_TEMPLATE/bug_report.yml
.github/workflows/ci.yml
.github/workflows/renderer-build.yml
.gitignore
CONTRIBUTING.md
LICENSE
LICENSES/GPL-3.0.txt
MANIFEST.in
Makefile
README.md
STATUS.md
build.sh
config/modules.toml
config/omniphony-headphones.yaml
docs/architecture.md
docs/audio-runtime.md
docs/automatic-diagnostics.md
docs/dependencies.md
docs/desktop-runtime.md
docs/equalizer.md
docs/feature-modules.md
docs/install.md
docs/live-audio.md
docs/orender-loopback.md
docs/publishing.md
docs/tidal-runtime.md
docs/tracker-runtime.md
docs/upstream-report.md
docs/validation-environment.md
docs/wireplumber-live-guard.md
examples/reconnect.jsonl
install.sh
packaging/69-spatiald-trackers.rules
packaging/PKGBUILD.in
packaging/README.md
packaging/companion-PKGBUILD.in
packaging/companion-PROVENANCE.md
packaging/orender-PKGBUILD.in
packaging/orender-Cargo.lock
packaging/orender-loopback.patch
packaging/spatiald.service
plasma/LiveAudioControls.qml
plasma/PlaybackControls.qml
plasma/PlaybackState.js
plasma/README.md
plasma/SpatialControls.qml
plasma/companion.patch
plasma/tests/CMakeLists.txt
plasma/tests/README.md
plasma/tests/qml-smoke.cpp
pyproject.toml
spatial/__init__.py
spatial/__main__.py
spatial/audio_runtime.py
spatial/cli.py
spatial/core.py
spatial/data/AutoEq-LICENSE.txt
spatial/data/wf1000xm5-dhrme-5band.json
spatial/data/wf1000xm5-dhrme-5band.txt
spatial/desktop.py
spatial/diagnostics_privacy.py
spatial/discovery.py
spatial/equalizer.py
spatial/errors.py
spatial/health.py
spatial/live_audio.py
spatial/mpris.py
spatial/osc.py
spatial/pipewire.py
spatial/playback.py
spatial/pose.py
spatial/preferences.py
spatial/processes.py
spatial/replay.py
spatial/retry.py
spatial/runtime.py
spatial/settings.py
spatial/tidal.py
spatial/trackers/__init__.py
spatial/trackers/common.py
spatial/trackers/hid.py
spatial/trackers/slime.py
spatial/trackers/sony.py
tests/test_audio_runtime.py
tests/test_control_unit.py
tests/test_core.py
tests/test_desktop.py
tests/test_diagnostics.py
tests/test_discovery.py
tests/test_equalizer.py
tests/test_health.py
tests/test_host_verification.py
tests/test_installer_contract.py
tests/test_live_audio.py
tests/test_mpris.py
tests/test_osc.py
tests/test_pipewire.py
tests/test_plasma.py
tests/test_playback.py
tests/test_processes.py
tests/test_retry.py
tests/test_runtime.py
tests/test_settings.py
tests/test_tidal.py
tests/test_trackers.py
tests/test_trackers_runtime.py
tests/test_wireplumber_guard.py
tools/diagnostics.py
tools/make-release.py
tools/publish-github.sh
tools/verify.py
wireplumber/scripts/spatial-live-guard.lua
wireplumber/wireplumber.conf.d/90-spatial-live-guard.conf
SOURCE_FILES
declare -A approved_paths=()
for source_file in "${source_files[@]}"; do
    approved_paths["$source_file"]=1
    [[ -f "$source_file" && ! -L "$source_file" ]] || fail "Missing or nonregular source file: $source_file"
    [[ $(realpath -- "$source_file") == "$project_dir/$source_file" ]] || fail "Source path passes through a symlink: $source_file"
done
if (( dry_run )); then
    printf 'Public repository name: %s\nAccount: %s\nReviewed source files (%s):\n' \
        "$repository_name" "${expected_owner:-the authenticated GitHub account}" "${#source_files[@]}"
    printf '  %s\n' "${source_files[@]}"
    printf 'No authentication, repository, index, commit, or remote was changed.\n'
    exit 0
fi
(( EUID != 0 )) || fail 'Run this script as your normal desktop user, not root.'
for git_variable in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES; do
    [[ -z ${!git_variable-} ]] || fail "Unset $git_variable before publishing this project."
done
missing_packages=()
command -v git >/dev/null 2>&1 || missing_packages+=(git)
command -v gh >/dev/null 2>&1 || missing_packages+=(github-cli)
if (( ${#missing_packages[@]} )); then
    command -v pacman >/dev/null 2>&1 || fail 'Install git and the official GitHub CLI (gh), then rerun this command.'
    command -v sudo >/dev/null 2>&1 || fail 'sudo is required to install missing packages.'
    sudo pacman -S --needed "${missing_packages[@]}"
fi

if existing_root=$(git rev-parse --show-toplevel 2>/dev/null); then
    [[ $(CDPATH= cd -P -- "$existing_root" && pwd) == "$project_dir" ]] || fail 'The project is nested inside another Git repository.'
    [[ -z $(git remote) ]] || fail 'This checkout already has a remote. Review it and publish updates with Git; this first-publication script will not replace it.'
    git diff --cached --quiet || fail 'The index has staged changes. Commit or unstage them yourself before publishing.'
else
    [[ ! -e .git ]] || fail 'An unreadable or unsupported Git repository already exists here.'
    git init --initial-branch=main
fi
git symbolic-ref --quiet HEAD >/dev/null || fail 'Check out a named branch before publishing.'

# A path whitelist for the working tree cannot protect unrelated files in old
# commits: inspect every tree reachable from the branch that will be pushed.
if git rev-parse --verify HEAD >/dev/null 2>&1; then
    commit_list=$(git rev-list HEAD)
    while IFS= read -r commit; do
        tree_listing=$(git ls-tree -r --full-tree "$commit")
        while IFS= read -r tree_entry; do
            [[ -n "$tree_entry" ]] || continue
            source_file=${tree_entry#*$'\t'}
            file_mode=${tree_entry%% *}
            [[ ${approved_paths["$source_file"]-} == 1 ]] || fail "Existing history includes a file outside the reviewed list: $source_file. Use a fresh source archive for first publication."
            [[ "$file_mode" == 100644 || "$file_mode" == 100755 ]] || fail 'Existing history contains a symlink or submodule. Use a fresh source archive for first publication.'
        done <<< "$tree_listing"
    done <<< "$commit_list"
fi

if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    gh auth login --hostname github.com --git-protocol https --web --scopes workflow
fi
profile=$(gh api --hostname github.com user --jq '[.login, (.id | tostring)] | @tsv')
IFS=$'\t' read -r account_login account_id <<< "$profile"
[[ "$account_login" =~ ^[A-Za-z0-9][A-Za-z0-9-]{0,38}$ && "$account_id" =~ ^[0-9]+$ ]] || fail 'GitHub did not return a valid authenticated personal account.'
[[ -z "$expected_owner" || "${expected_owner,,}" == "${account_login,,}" ]] || fail "Signed in as $account_login, but --owner requires $expected_owner. Use gh auth switch, then rerun."
repository="$account_login/$repository_name"
if gh api --hostname github.com "repos/$repository" --silent >/dev/null 2>&1; then
    fail "https://github.com/$repository already exists. Nothing will be overwritten; choose a different --name or update the existing repository with Git."
fi

# Preserve configured Git identity; only fill missing fields in this checkout.
[[ -n $(git config --get user.name || true) ]] || git config --local user.name "$account_login"
[[ -n $(git config --get user.email || true) ]] || git config --local user.email "$account_id+$account_login@users.noreply.github.com"
git add -- "${source_files[@]}"
if ! git diff --cached --quiet; then
    git commit -m 'Publish Spatial Workbench source'
fi
git rev-parse --verify HEAD >/dev/null || fail 'There is no source commit to publish.'
gh auth setup-git --hostname github.com
printf 'Publishing PUBLIC repository: https://github.com/%s\n' "$repository"
if ! GH_HOST=github.com gh repo create "$repository" --public --source "$project_dir" --remote origin --push \
    --description 'Modular Sony XM5 spatial audio and head tracking for Arch Linux and KDE Plasma'; then
    printf 'Publication did not finish. The local commit is retained. If GitHub created the\nrepository before the push failed, review its origin and use a normal git push;\nthis script will not replace an existing remote or force-push. See docs/publishing.md.\n' >&2
    exit 1
fi
printf 'Published: https://github.com/%s\n' "$repository"
