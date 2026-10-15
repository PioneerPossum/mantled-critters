import socket, struct, time, sys
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4<<20)
s.bind(("0.0.0.0", 6990)); s.settimeout(1.0); seen=set(); maxseq=-1; t0=time.time()
DUR=float(sys.argv[1]) if len(sys.argv)>1 else 14
while time.time()-t0 < DUR:
    try: d,_=s.recvfrom(2048)
    except socket.timeout: continue
    q=struct.unpack("<I", d[:4])[0]; seen.add(q); maxseq=max(maxseq,q)
print(f"received {len(seen)} of {maxseq+1} ({100*(1-len(seen)/max(maxseq+1,1)):.2f}% lost)")
