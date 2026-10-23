#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
[ -f worker/cicd.pid ] && kill -- -"$(ps -o pgid= "$(cat worker/cicd.pid)" | tr -d ' ')" 2>/dev/null && echo "cicd stopped" || echo "cicd not running"
rm -f worker/cicd.pid
