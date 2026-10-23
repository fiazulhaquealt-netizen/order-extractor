#!/usr/bin/env bash
# Supervisor for worker/cicd.py: restarts it if it exits. Start: nohup bash worker/cicd_forever.sh >/dev/null 2>&1 &
# Stop: bash worker/cicd_stop.sh     Log: worker/cicd.log
cd "$(dirname "$0")/.." || exit 1
exec 8>"worker/.cicd.lock"; flock -n 8 || { echo "cicd already running"; exit 0; }
echo $$ > worker/cicd.pid
trap 'kill 0; exit 0' INT TERM
export PATH="$HOME/.local/bin:$PATH"
while true; do
  python3 -u worker/cicd.py >> worker/cicd.log 2>&1 &
  wait $!
  echo "$(date '+%F %T') cicd.py exited rc=$?; restarting in 5s" >> worker/cicd.log
  sleep 5
done
