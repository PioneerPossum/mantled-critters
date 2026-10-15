#!/bin/bash
# run.sh N W H FPS MEASURE_SECS
N=$1; W=$2; H=$3; F=$4; S=$5
cd /home/sinewave/.claude/jobs/386af2e4/tmp
pids=()
for i in $(seq 1 $N); do nice -n 10 ./venv/bin/python ndi_send.py s$i $W $H $F $((S+40)) > snd_$i.txt 2>&1 & pids+=($!); done
sleep 8
ports=""
for p in "${pids[@]}"; do pt=$(ss -ltnpH "( sport >= :5960 and sport <= :6100 )" | grep "pid=$p," | grep -oP '0\.0\.0\.0:\K[0-9]+|\*:\K[0-9]+' | sort -n | tail -1); ports="$ports 192.168.8.136:$pt"; done
echo "=== $N x ${W}x${H}p$F === ports:$ports"
ssh balthazar "ndi-bench/bench.sh '$ports' $S 10"
kill "${pids[@]}" 2>/dev/null; wait; grep -h sent snd_*.txt | head -2; rm -f snd_*.txt
