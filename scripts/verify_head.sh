#!/usr/bin/env bash
# The nightly must run the code on origin/main. Trials-registry commits pushed by research workflows
# since the checkout (scripts/persist_trials.py) touch only config/research_trials.jsonl and are allowed.
set -euo pipefail
git fetch -q origin main
local_head=$(git rev-parse HEAD)
remote_head=$(git rev-parse origin/main)
echo "Local HEAD:  $local_head"
echo "Remote HEAD: $remote_head"
if [ "$local_head" = "$remote_head" ]; then
  echo "HEAD matches origin/main"
  exit 0
fi
if git merge-base --is-ancestor "$local_head" "$remote_head"; then
  others=$(git log --name-only --format= "$local_head..$remote_head" | sort -u | grep -v -x -e "config/research_trials.jsonl" -e "" || true)
  if [ -z "$others" ]; then
    echo "origin/main is ahead only by trials-registry commits: OK"
    exit 0
  fi
  echo "origin/main has newer commits touching: $others"
fi
echo "ERROR: Local checkout does not match origin/main!"
exit 1
