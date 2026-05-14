#!/usr/bin/env bash
# Start the ETH reversal alert bot in the background and tail its log.
# Usage: bash run_alert.sh

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/eth_alert.log"

echo "Starting ETH Reversal Alert Bot…"
echo "Logs: $LOG"
echo "Stop with: kill \$(cat $DIR/eth_alert.pid)"
echo ""

nohup python3 "$DIR/eth_reversal_alert.py" >> "$LOG" 2>&1 &
echo $! > "$DIR/eth_alert.pid"
echo "PID: $(cat $DIR/eth_alert.pid)"
tail -f "$LOG"
