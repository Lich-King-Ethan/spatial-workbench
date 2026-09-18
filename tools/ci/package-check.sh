#!/usr/bin/env bash
# Keep makepkg's actual failure visible in the job summary and an artifact.
# Usage: package-check.sh PACKAGE LOG_DIR -- COMMAND [ARG ...]
set -euo pipefail

if (( $# < 4 )) || [[ $3 != -- ]] || [[ ! $1 =~ ^[[:alnum:]_.-]+$ ]]; then
  printf '%s\n' 'Usage: package-check.sh PACKAGE LOG_DIR -- COMMAND [ARG ...]' >&2
  exit 2
fi

package=$1
log_dir=$2
shift 3
mkdir -p -- "$log_dir"
log_file="$log_dir/$package.log"

printf 'Checking package %s; full output: %s\n' "$package" "$log_file"
set +e
"$@" 2>&1 | tee -- "$log_file"
pipeline_status=("${PIPESTATUS[@]}")
set -e
status=${pipeline_status[0]}
if (( status == 0 && pipeline_status[1] != 0 )); then
  status=${pipeline_status[1]}
fi

write_summary() {
  printf '### Package `%s`: ' "$package"
  if (( status == 0 )); then
    printf 'passed\n\n'
    return
  fi
  printf 'failed (exit %s)\n\n' "$status"
  printf '%s\n\n' 'Download the package-check log artifact for the complete output.'
  printf '%s\n\n' '<details><summary>First errors and final output</summary>'
  printf '%s\n' '<pre>'
  {
    awk '
      { lines[NR] = $0 }
      /(^|[^[:alpha:]])(FAIL!?|FAILED|ERROR|AssertionError|Traceback)([^[:alpha:]]|$)|[Ee]rror:/ {
        if (count < 4 && (count == 0 || NR > last + 12)) {
          errors[++count] = NR
          last = NR
        }
      }
      END {
        for (e = 1; e <= count; e++) {
          first = errors[e] > 8 ? errors[e] - 8 : 1
          final = errors[e] + 12 < NR ? errors[e] + 12 : NR
          printf "--- error context, lines %d-%d ---\n", first, final
          for (i = first; i <= final; i++) print lines[i]
        }
      }
    ' "$log_file"
    printf '%s\n' '--- final 60 lines ---'
    tail -n 60 -- "$log_file"
  } | awk 'length($0) > 2000 { print substr($0, 1, 2000) " [line truncated; see full log]"; next } { print }' \
    | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
  printf '%s\n\n' '</pre></details>'
}

if [[ -n ${GITHUB_STEP_SUMMARY:-} ]]; then
  write_summary >> "$GITHUB_STEP_SUMMARY" || printf '%s\n' 'Could not write the GitHub job summary.' >&2
fi
if (( status != 0 )); then
  printf '::error title=Package %s failed::Package %s failed with exit %s. See the job summary and package-check log artifact.\n' "$package" "$package" "$status"
fi
exit "$status"
