import socket, struct, time
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4<<20)
s.bind(("0.0.0.0", 6990)); s.settimeout(1.0); seen=set(); src=set(); t0=time.time(); first=None; last=None; prev=None; maxgap=0; gaps=0
while time.time()-t0 < 300:
    try: d,a=s.recvfrom(2048)
    except socket.timeout:
        if last and time.time()-last > 3: break
        continue
    now=time.time(); q=struct.unpack("<I", d[:4])[0]; seen.add(q); src.add(a[0])
    if first is None: first=now
    if prev is not None and now-prev > 0.05: gaps+=1; maxgap=max(maxgap, now-prev)
    prev=now; last=now
mx=max(seen)+1 if seen else 0
print(f"from={src} received {len(seen)} of {mx} ({100*(1-len(seen)/max(mx,1)):.2f}% lost) span={((last or 0)-(first or 0)):.2f}s stalls>50ms={gaps} max_stall={maxgap*1000:.0f}ms")
