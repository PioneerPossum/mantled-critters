#!/bin/bash
# usage: bench.sh "url1 url2 ..." SECS WARM
URLS=$1; SECS=$2; WARM=$3
cd ~/ndi-bench
rxb() { echo $(( $(cat /sys/class/net/eth0/statistics/rx_bytes) + $(cat /sys/class/net/eth1/statistics/rx_bytes) )); }
cpu() { awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $5+$6}' /proc/stat; }
i=0; for u in $URLS; do i=$((i+1)); nice -n 10 ./venv/bin/python ndi_recv.py $u $SECS $WARM x > out_$i.txt 2>&1 & done
sleep $((WARM+2))
read b1 i1 < <(cpu); rx1=$(rxb); t1=$(date +%s.%N)
maxt=0
for k in $(seq 1 $(( (SECS-4)/2 ))); do
  t=$(vcgencmd measure_temp | grep -oP '[0-9.]+'); maxt=$(echo "$t $maxt" | awk '{print ($1>$2)?$1:$2}'); sleep 2
done
read b2 i2 < <(cpu); rx2=$(rxb); t2=$(date +%s.%N)
wait
cat out_*.txt | sort -V; rm -f out_*.txt
awk -v b1=$b1 -v b2=$b2 -v i1=$i1 -v i2=$i2 -v r1=$rx1 -v r2=$rx2 -v t1=$t1 -v t2=$t2 -v mt=$maxt \
 'BEGIN{busy=b2-b1; idle=i2-i1; printf "SUMMARY cpu_busy=%.1f%% (of 4 cores) maxtemp=%sC rx=%.1f Mbit/s\n", 100*busy/(busy+idle), mt, (r2-r1)*8/(t2-t1)/1e6}'
echo "throttled: $(vcgencmd get_throttled)  freq: $(vcgencmd measure_clock arm)"; uptime
