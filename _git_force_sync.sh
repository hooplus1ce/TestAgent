#!/usr/bin/env bash
# Temporary helper from a failed automated git sync attempt.
# Safe to delete. Preferred one-liner when shell works:
#   git fetch origin && git checkout main && git reset --hard origin/main && git clean -fd
echo "delete this file; run the comment above if you still need a hard reset"
