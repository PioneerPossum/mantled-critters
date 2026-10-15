import socket, time, struct
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4<<20); s.bind(("0.0.0.0", 6980)); s.settimeout(0.5)
end=time.time()+10; first=last=None; n=0; gaps=0; missing=0; ts=[]; maxgap=0
while time.time()<end:
    try: d,a=s.recvfrom(2048)
    except socket.timeout: continue
    fc=struct.unpack("<I", d[24:28])[0]; t=time.time(); n+=1
    if last is not None and fc!=last+1: gaps+=1; missing+=fc-last-1
    if ts: maxgap=max(maxgap, t-ts[-1])
    first=fc if first is None else first; last=fc; ts.append(t)
span=(last-first+1) if n else 0
print(f"received={n} frame_counter_span={span} gaps={gaps} missing={missing} ({100*missing/max(span,1):.1f}% lost) max_interarrival={maxgap*1000:.0f}ms")
if n>1: print(f"sender frame rate implied by counter = {span/(ts[-1]-ts[0]):.0f}/s")
