import socket, time, struct, collections
SR=[6000,12000,24000,48000,96000,192000,384000,8000,16000,32000,64000,128000,256000,512000,11025,22050,44100,88200,176400,352800,705600]
s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); s.bind(("0.0.0.0", 6980)); s.settimeout(0.5)
end=time.time()+10; n=0; b=0; src=collections.Counter(); hdr=None
while time.time()<end:
    try: d,a=s.recvfrom(2048)
    except socket.timeout: continue
    n+=1; b+=len(d); src[a[0]]+=1
    if hdr is None and d[:4]==b"VBAN":
        sr=d[4]&0x1f; proto=d[4]>>5; nsamp=d[5]+1; ch=d[6]+1; fmt=d[7]&0x07; name=d[8:24].split(b"\0")[0].decode(errors="replace")
        hdr=f"proto={proto} rate={SR[sr] if sr<len(SR) else sr} ch={ch} samples/pkt={nsamp} fmt={fmt} stream='{name}' pktlen={len(d)}"
print(f"packets={n} ({n/10:.0f}/s) bytes={b} ({b*8/10/1e6:.2f} Mbit/s) sources={dict(src)}"); print(hdr)
