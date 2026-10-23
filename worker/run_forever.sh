#!/usr/bin/env bash
# Watchdog: restarts tick_loop.sh if it exits. Stop: kill the PID in worker/run_forever.pid (kills loop + children via group).
cd "$(dirname "$0")/.." || exit 1
echo $$ > worker/run_forever.pid
trap 'kill 0; exit 0' INT TERM
export WORKER_INTERVAL_S=15 PATH="$HOME/.local/bin:$PATH"
while true; do
  echo "$(date '+%F %T') tick_loop start" 
  bash worker/tick_loop.sh &
  wait $!
  echo "$(date '+%F %T') tick_loop exited rc=$?; restarting in 5s"
  sleep 5
done
