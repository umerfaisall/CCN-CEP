"""CS-327 CEP R6 benchmark. Raw-socket WebSocket client; no libraries.

    python bench.py <server_ip> [port] [runs]      e.g.  python bench.py 192.168.1.10 5000 5

Prints mean / stdev / min / max over `runs` repetitions (>=5 for the report) and writes bench_results.csv.
Run it once per condition (e.g. wired vs Wi-Fi) from a different host and compare the tables.
"""
import base64, csv, json, os, socket, statistics as st, struct, sys, threading, time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 5
tag = str(os.getpid())


def send(s, obj):
    body = json.dumps(obj).encode()
    n, mask = len(body), os.urandom(4)
    head = bytes([0x81, 0x80 | n]) if n < 126 else bytes([0x81, 0xFE]) + struct.pack("!H", n)
    s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(body)))


def recv(f):
    n = f.read(2)[1] & 0x7F
    if n == 126:
        n = struct.unpack("!H", f.read(2))[0]
    return json.loads(f.read(n))


def wait(f, kind):
    while (m := recv(f))["type"] != kind:
        pass
    return m


def connect(name):
    """TCP connect + WebSocket upgrade + LOGIN. Returns (socket, reader)."""
    s = socket.create_connection((HOST, PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(f"GET /ws HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
    f = s.makefile("rb")
    while f.readline() not in (b"\r\n", b""):
        pass
    send(s, {"type": "LOGIN", "username": name})
    wait(f, "LOGIN_OK")
    return s, f


def ms(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


def rtt(s, f, n=20):  # application-level round trip: PING -> PONG
    return st.mean(ms(lambda: (send(s, {"type": "PING", "id": 0}), wait(f, "PONG"))) for _ in range(n))


def relay(a, fa, b, fb, n=20):  # response time of one chat operation: A sends, B receives
    def one():
        send(a, {"type": "CHAT", "scope": "private", "recipient": f"b{tag}", "text": "x" * 100})
        wait(fb, "CHAT")
        wait(fa, "CHAT")  # A's own echo, keeps A's buffer drained
    return st.mean(ms(one) for _ in range(n))


def throughput(a, fa, b, fb, size, n=200):  # KB/s of chat payload from A through the server to B
    done = threading.Event()

    def drain(f):
        got = 0
        while got < n:
            got += recv(f)["type"] == "CHAT"
        done.set() if f is fb else None
    for f in (fa, fb):
        threading.Thread(target=drain, args=(f,), daemon=True).start()
    t = time.perf_counter()
    for _ in range(n):
        send(a, {"type": "CHAT", "scope": "private", "recipient": f"b{tag}", "text": "x" * size})
    done.wait(60)
    return n * size / 1024 / (time.perf_counter() - t)


def new_connection(i):  # persistent-vs-new-connection comparison: cost of TCP + upgrade + LOGIN
    t = time.perf_counter()
    s, _ = connect(f"tmp{tag}_{i}")
    dt = (time.perf_counter() - t) * 1000
    s.close()
    return dt


rows = []


def measure(label, unit, fn):
    vals = [fn(i) for i in range(RUNS)]
    sd = st.stdev(vals) if len(vals) > 1 else 0
    rows.append([label, unit, f"{st.mean(vals):.2f}", f"{sd:.2f}", f"{min(vals):.2f}", f"{max(vals):.2f}"] + [f"{v:.2f}" for v in vals])
    print(f"{label:<42}{unit:<7}{st.mean(vals):>9.2f} ±{sd:<7.2f} min {min(vals):.2f}  max {max(vals):.2f}")


print(f"server {HOST}:{PORT}, {RUNS} runs each\n{'measurement':<42}{'unit':<7}{'mean':>9}  stdev")
a, fa = connect(f"a{tag}")
b, fb = connect(f"b{tag}")
measure("RTT, PING->PONG on open connection", "ms", lambda i: rtt(a, fa))
measure("new connection (TCP+upgrade+LOGIN_OK)", "ms", new_connection)
measure("chat relay A->server->B, 100 B", "ms", lambda i: relay(a, fa, b, fb))
measure("throughput, 200 x 50 B messages", "KB/s", lambda i: throughput(a, fa, b, fb, 50))
measure("throughput, 200 x 1000 B messages", "KB/s", lambda i: throughput(a, fa, b, fb, 1000))

with open("bench_results.csv", "w", newline="") as fh:
    csv.writer(fh).writerows([["measurement", "unit", "mean", "stdev", "min", "max"] + [f"run{i + 1}" for i in range(RUNS)]] + rows)
print("\nsaved bench_results.csv")
