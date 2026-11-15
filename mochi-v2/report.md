# Mochi Chat: Design, Implementation and Evaluation of a Multi-Host Chat Application

**CS-327 Computer Communication Networks, Term Project (Complex Engineering Problem), Fall 2026**
**NED University of Engineering & Technology, Department of CSE**
**Option D: Multi-client chat with presence**

| Member | Roll number |
|---|---|
| **[TODO: name]** | **[TODO: CS24XXX]** |
| **[TODO: name]** | **[TODO: CS24XXX]** |
| **[TODO: name]** | **[TODO: CS24XXX]** |
| **[TODO: name]** | **[TODO: CS24XXX]** |

---

## 1. Introduction and option selected

We built **Mochi Chat**, a multi-client chat system with presence (Option D). A central relay server
runs on one host. Clients on other hosts open the chat in a web browser served by that same server
and exchange messages through it in real time. The system supports:

- a public **lobby**, **private** one-to-one chats and **groups** (create, add members);
- **presence**: an online-user list plus JOINED/LEFT notifications;
- **typing indicators**, user **profiles** (display name, bio, avatar) and a short **history** replay on login;
- **consistent message ordering** when several users send at the same instant, using a server-assigned sequence number.

**Why Option D.** It exposes the networking problems the course emphasises:
- many concurrent TCP connections on one server;
- application-level multicast (one message relayed to many sockets);
- message boundaries over a byte stream;
- ordering under concurrency;
- failure detection when a peer disappears.

It also yields natural R6 comparisons (persistent vs new connection, small vs large messages, idle vs
loaded Wi-Fi) and at least three real design tensions (§7).

**Constraints we set ourselves.**
- Python **standard library only** on the server, with no web or WebSocket framework. The server
  implements HTTP request parsing, the WebSocket handshake and framing by hand on `socket` (§4.1).
- Our own message protocol (§2).
- No hardcoded addresses: `--host` / `--port` command-line options (default `0.0.0.0:5000`).
- A millisecond-timestamped log of every message sent and received, used as evidence for R6 and R7.

The system went through two versions: `mochi-v1/` (a single-file server) and `mochi-v2/` (the
submitted version: the same protocol, the server split into ten single-purpose modules, and a
redesigned UI). This report describes **v2**.

---

## 2. Protocol specification (R1)

### 2.1 Layering and framing

| Layer | What we use | Who implements it |
|---|---|---|
| Application | **Mochi protocol**: JSON messages defined below | Us (server `handlers.py`, client `app.js`) |
| Message framing | WebSocket frames, RFC 6455 (one JSON object per text frame) | Us on the server (`ws_framing.py`); the browser's built-in WebSocket on the client |
| Connection setup | HTTP/1.1 `GET /ws` with `Upgrade: websocket` | Us (`http_static.py`, `connection.py`) |
| Transport | TCP, server port **5000** | Operating system |
| Network | IPv4 | Operating system |

**Why framing is needed.** TCP delivers a byte stream, not messages: one `recv()` may return half a
message or two messages joined together. Every message therefore travels inside a WebSocket frame,
whose header carries its length. The receiver reads exactly that many bytes (`read_exact`).

**Frame header** (an implementer needs this to talk to our server without a browser; `bench.py` does exactly that):

| Byte(s) | Field | Value / meaning |
|---|---|---|
| 0, bit 7 | FIN | 1 (we never fragment messages) |
| 0, bits 3–0 | Opcode | `0x1` text (all Mochi messages), `0x8` close, `0x9` ping, `0xA` pong |
| 1, bit 7 | MASK | 1 on client→server frames (mandatory), 0 on server→client |
| 1, bits 6–0 | Length | 0–125 = payload length; 126 = next 2 bytes hold the length; 127 = next 8 bytes |
| next 0/2/8 | Extended length | Unsigned, network byte order (big-endian) |
| next 4 | Masking key | Client frames only; payload byte *i* is XOR-ed with key byte *i mod 4* |
| rest | Payload | UTF-8 JSON object |

The server rejects frames larger than 1 000 000 bytes (`MAX_FRAME`) and closes the connection.

**Handshake.** The client sends `GET /ws HTTP/1.1` with `Upgrade: websocket`, `Connection: Upgrade`
and a random 16-byte base64 `Sec-WebSocket-Key`. The server replies `HTTP/1.1 101 Switching Protocols`
with `Sec-WebSocket-Accept = base64(SHA-1(key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))`. Any other
path on the same port is a normal HTTP GET for `index.html`, `style.css` or `app.js`.

### 2.2 Message format

Every message is a JSON object with a mandatory `"type"` field (a string). The other fields depend on
the type. Field names are case-sensitive. Unknown extra fields are ignored. Times are Unix seconds
(float, server clock).

**Limits:**
- username: 1–20 printable characters, unique among online users, compared case-insensitively;
- display name ≤ 32 characters;
- bio ≤ 160;
- avatar ≤ 200 000 characters, either `""` or a `data:image/(png|jpeg|webp|gif);base64,…` URL;
- chat text 1–1000 characters (after trimming it must not be empty);
- group name 1–40 characters.

### 2.3 Message types and action on receipt

**Client → server**

| Type | Fields | Server action on receipt |
|---|---|---|
| `LOGIN` | `username`, `display_name`? | Validate the name. Atomically check it is not online and register the session. Reply `LOGIN_OK`. Broadcast `PRESENCE JOINED` and `USER_LIST`. Send this client its `GROUP_LIST` and a `HISTORY` (if any) |
| `PROFILE_UPDATE` | `display_name`, `bio`, `avatar` | Validate types, lengths and avatar format. Update the profile (an empty name falls back to the username). Reply `PROFILE_OK`. Broadcast `USER_LIST` |
| `CHAT` | `text`, `scope` (`public`/`private`/`group`), `recipient` (private) / `group_id` (group) | Validate the text. Choose recipients by scope. Assign the next `seq`. Store in history. Relay the `CHAT` to the recipients (the sender is included, as the delivery confirmation) |
| `TYPING` | `scope`, `recipient`/`group_id`, `is_typing` (bool) | Same routing as CHAT. Relay to the other participants only. **Invalid targets are dropped silently** (advisory, high-frequency) |
| `GROUP_CREATE` | `name` | Create the group with the caller as its only member. Reply `GROUP_CREATED`, then `GROUP_LIST` |
| `GROUP_ADD_MEMBER` | `group_id`, `username` | The caller must be a member and the target must be online. Add the target. Send `GROUP_LIST` to every online member. Send the target that group's `HISTORY` |
| `PING` | `id` (any JSON value) | Reply `PONG` with the same `id` and the server time |

**Server → client**

| Type | Fields | Sent to / when | Client action |
|---|---|---|---|
| `LOGIN_OK` | `username`, `server_time` | Requester, after a valid LOGIN | Enter the chat view; reset the reconnect back-off |
| `USER_LIST` | `users[{username, display_name, bio, avatar}]` | Everyone, on every join, leave and profile change | Replace the online list |
| `PRESENCE` | `event` (`JOINED`/`LEFT`), `username`, `seq`, `timestamp` | Everyone | Show a notice in the lobby |
| `PROFILE_OK` | `profile` | Requester | Update its own card |
| `CHAT` | `username`, `text`, `scope`, `seq`, `timestamp`, `recipient`?/`group_id`? | Public: everyone. Private: sender + recipient. Group: online members | Store in that conversation; show it, or add an unread badge and a notification |
| `TYPING` | as received + `username` | Other participants | Show "X is typing"; expire after 4 s if no `false` arrives |
| `HISTORY` | `messages[]` (CHAT packets, ≤ 200 newest) | One client, after login or after being added to a group | Merge without notifications; skip messages whose `seq` it already has |
| `GROUP_CREATED` | `id`, `name` | Creator | Open the new group |
| `GROUP_LIST` | `groups[{id, name, members[]}]` | Each client separately, **only the groups it belongs to** | Replace its group list |
| `PONG` | `id`, `server_time` | Requester | RTT = now − `id`; shown under the user's name |
| `ERROR` | `code`, `message` | Requester | Show `message`. On a login error, return to the login form |

**History privacy rule:** a user receives lobby messages, private messages they sent or received, and
messages of groups they are currently a member of. Nothing else is ever replayed.

### 2.4 Legal message order

![Figure 2.1: legal message order](figures/protocol_order.png)

**Figure 2.1.** One connection from start to finish, read from top to bottom. Editable source:
`figures/protocol_order.drawio`.

Rules the figure does not show:

- **Before login:** any message other than `LOGIN` is answered with `ERROR LOGIN_REQUIRED`. The
  connection stays open and the client is still in phase 2.
- **A failed login** (invalid or taken name) also leaves the client in phase 2, so it may send
  `LOGIN` again.
- **`HISTORY`** is sent only if there is at least one message the user may see.
- **There is no LOGOUT message:** closing the connection *is* the logout. One connection carries
  exactly one session.

### 2.5 Error codes

| Code | Cause |
|---|---|
| `LOGIN_REQUIRED` | Any message before a successful LOGIN |
| `BAD_JSON` | Text frame is not valid UTF-8 JSON |
| `INVALID_MESSAGE` | JSON is valid but not an object |
| `UNKNOWN_TYPE` | `type` is not one of the client→server types |
| `INVALID_USERNAME` | Empty, longer than 20, or contains non-printable characters |
| `USERNAME_TAKEN` | The same name (case-insensitively) is online |
| `EMPTY_MESSAGE` | CHAT text missing, not a string, or only whitespace |
| `MESSAGE_TOO_LONG` | CHAT text over 1000 characters |
| `INVALID_SCOPE` | Scope is not public/private/group |
| `INVALID_RECIPIENT` | Private chat to yourself or with a missing/non-string recipient |
| `USER_OFFLINE` | Private recipient or user to be added is not online (no offline delivery) |
| `INVALID_PROFILE` | A profile field is not a string, or the avatar is not an allowed data URL |
| `PROFILE_TOO_LARGE` | A profile field exceeds its limit |
| `INVALID_GROUP_NAME` | Group name empty or over 40 characters |
| `NOT_GROUP_MEMBER` | Group does not exist or the caller is not a member |

Every error is reported to the requester only; the connection stays open. Only transport-level faults
close it: oversized frames, a broken socket, or a silent HTTP connection after 30 s.

### 2.6 Ordering guarantee

`seq` is a single counter incremented for every `CHAT` and `PRESENCE`. It is incremented **and the
packet is emitted while the server holds one global lock**. If Ali and Sara send at the same instant,
one acquires the lock first, gets the lower `seq`, and its packet is written to every socket before
the other's. TCP preserves order within each connection, so **every client sees all events in the
same order**. The client also sorts replayed history by `seq`.

---

## 3. System and deployment architecture (R3)

### 3.1 Architecture

![Figure 3.0: server architecture](figures/architecture.png)

**Figure 3.0.** Architecture of the server process on Host A. Each box is one Python module, and each
arrow shows what one module passes to another. Editable source: `figures/architecture.drawio`.

How to read it, top to bottom:

1. A browser on Host B or C connects to TCP port 5000. `main.py` accepts the connection and starts a
   thread for it.
2. `connection.py` reads the first HTTP request. A plain `GET` for a page file goes to `http_static.py`;
   `GET /ws` becomes a WebSocket connection, framed by `ws_framing.py`.
3. For every JSON message that arrives, `handlers.py` validates it, chooses the recipients and replies.
   It reads and changes the shared data in `state.py`, which sits behind one global lock.
4. `broadcast.py` builds the messages the server sends by itself (`USER_LIST`, `GROUP_LIST`, `PRESENCE`,
   `HISTORY`). `outbox.py` turns every outgoing message into a frame and writes it to the clients' sockets.
5. `logger.py` records every message with a millisecond timestamp in `logs/server.log`.

The server is a relay. Clients never talk to each other directly, which keeps presence and ordering
in one place (see §7, State vs simplicity).

### 3.2 Network diagram and IP plan

![Figure 3.1: network diagram](figures/network_diagram.png)

**Figure 3.1.** Three hosts on one Wi-Fi LAN, with the TCP connections to the server on port 5000.
Editable source: `figures/network_diagram.drawio`.

| Host | Role | OS | Interface | IPv4 address / mask | Default gateway |
|---|---|---|---|---|---|
| A | Server | Windows 11 (build 26200) | Wi-Fi | `192.168.100.126` / `255.255.255.0` | `192.168.100.1` |
| B | Client | Windows 11 (build 22621) | Wi-Fi | `192.168.100.50` / `255.255.255.0` | `192.168.100.1` |
| C | Client | Windows | Wi-Fi | `192.168.100.147` / `255.255.255.0` | `192.168.100.1` |

All three addresses are distinct and lie in `192.168.100.0/24` (mask `255.255.255.0`), so the hosts are on
the same LAN behind one router. None of them uses `127.0.0.1`.

![Figure 3.2: ipconfig output of the three hosts](figures/ipconfig_hosts.png)

**Figure 3.2.** `ipconfig` on each host (Wi-Fi adapter, IPv4 line boxed), taken on 2026-11-15 between 14:29
and 14:44. Full screenshots: `capture/`.

**Evidence of real multi-host operation** (`logs/server.log`):

```
2026-11-14 11:43:57.009 open http://localhost:5000 here, or http://192.168.100.126:5000 from other hosts
2026-11-14 11:48:03.783 192.168.100.50:49845 websocket open
2026-11-14 11:48:03.793 JOINED Umerr from 192.168.100.50:49845
2026-11-14 11:48:08.428 CHAT seq=4 Umerr -> public (2 chars, 2 recipients)
2026-11-14 11:48:30.065 CHAT seq=6 ahmed -> public (4 chars, 2 recipients)
2026-11-15 14:27:39.437 192.168.100.147:49735 websocket open
2026-11-15 14:27:39.802 JOINED Jia from 192.168.100.147:49735
2026-11-15 14:36:25.183 192.168.100.147:49735 closed (Peer closed connection unexpectedly)
```

The first five lines show Host A serving Host B. The last three show Host C joining as `Jia` and leaving.

**Deployment notes we hit in practice:**
- The server must bind `0.0.0.0`, not `127.0.0.1`, or other hosts cannot connect.
- Windows Firewall must allow inbound TCP 5000 (the network profile set to *Private*).
- Some campus and public Wi-Fi networks enable **client isolation**, which blocks host-to-host
  traffic. A phone hotspot or our own router avoids it.

**Demo start-up:** on Host A, `cd mochi-v2/backend` then `py main.py`. The server prints the URL the
other hosts must open (`http://<A's IP>:5000`).

---

## 4. Implementation notes and transport justification (R2, R4)

### 4.1 Raw sockets, no protocol libraries (R2)

The server uses only `socket`, `threading`, `json`, `hashlib`, `base64`, `struct`, `urllib.parse`,
`argparse` and `time`. **No** `http.server`, `websockets`, `asyncio` frameworks or similar.

| Concern | Where | How |
|---|---|---|
| Listening, accepting | `main.py` | `socket(AF_INET, SOCK_STREAM)`, `SO_REUSEADDR`, `bind`, `listen(50)`, `accept()` loop |
| Concurrency | `main.py` | One daemon thread per connection |
| Socket options | `connection.py` | `TCP_NODELAY` (disable Nagle: small chat frames go out immediately); `SO_KEEPALIVE`; 30 s timeout until the connection identifies itself |
| HTTP parsing | `http_static.py` | Read until `\r\n\r\n` (max 16 KB). Parse the request line and headers by hand. Serve only 3 whitelisted files; path-traversal check |
| WebSocket handshake, framing | `ws_framing.py` | Accept-key computation; frame encode with 7/16/64-bit lengths; `read_exact` loop; XOR unmasking |
| Shared state | `state.py` | `clients`, `groups`, `history` (deque of 500), `seq`, one `RLock` |
| Output | `outbox.py` | `emit` encodes the JSON and builds the frame **once**, then writes it to each recipient; per-socket write lock so two threads never interleave bytes on one socket |
| Protocol logic | `handlers.py` | Dispatch table `type → handler`; validation; `route()` chooses recipients by scope |
| Server-originated messages | `broadcast.py` | USER_LIST, GROUP_LIST, PRESENCE, HISTORY |

Module dependency is one-way (main → connection → handlers → broadcast → outbox → ws_framing →
config), so there are no import cycles.

**Honest note on the client.** The browser client uses the browser's built-in `WebSocket` object,
which implements the frame layer (not our application protocol) on the client side. Browsers cannot
open raw TCP sockets. To show that the protocol does not depend on it, `bench.py` is a second client
written on raw sockets that implements the handshake, masking and framing itself, with no libraries.

### 4.2 Concurrency design

- A **thread per client** keeps the code readable: each thread blocks in `recv()` for its own client.
- **One global lock** protects all shared state. Every handler that reads or changes state, assigns a
  `seq` or broadcasts runs inside it. This is what makes ordering consistent (§2.6), and LOGIN's
  check-then-insert atomic, so two simultaneous logins with the same name cannot both succeed.
- **Per-socket write locks** stop frames from two threads (e.g. a CHAT relay and a PONG) from
  interleaving on one socket.
- **Cost:** sends happen while holding the global lock, so a client with a full TCP send buffer (very
  slow link) can briefly stall everyone. We chose ordering over isolation (§7).

### 4.3 Transport choice per message type (R4)

All Mochi messages travel over **TCP** (one long-lived connection per client). The choice was made
per message type against Lecture 04's four service requirements:

| Message type | Reliable transfer | Throughput | Timing | Security | Choice and what UDP would lose |
|---|---|---|---|---|---|
| `CHAT` | **Essential**: a lost or duplicated chat message is a visible error | Low (≤ 1 KB) | Interactive (~100s of ms acceptable) | Not provided by either | **TCP.** Our ordering guarantee *relies* on TCP's in-order delivery per connection. With UDP we would have to add our own acknowledgements, retransmission timers, duplicate detection and reordering by `seq`, i.e. rebuild TCP badly |
| `PRESENCE`, `USER_LIST`, `GROUP_LIST` | **Essential**: these are state updates; a lost one leaves a client's view permanently wrong until the next change | `USER_LIST` with avatars can reach hundreds of KB | Not critical | – | **TCP.** UDP loss would cause silent divergence; UDP also cannot carry a datagram larger than 65 507 B, and larger IP datagrams fragment (one lost fragment loses the whole datagram) |
| `PROFILE_UPDATE` (avatar ≤ 200 KB) | Essential | Bulk transfer | Not critical | – | **TCP.** Needs segmentation, flow and congestion control; with UDP we would implement fragmentation and reassembly ourselves |
| `LOGIN` / `LOGIN_OK`, `ERROR`, `GROUP_*` | Essential (request/response) | Tiny | Not critical | – | **TCP.** UDP would need request timeouts and retries plus idempotency (a retried GROUP_CREATE could create two groups) |
| `TYPING` | **Not needed**: advisory; a lost indicator only means "typing…" appears late | Tiny but frequent | Should be timely | – | **UDP would be the better fit in principle**: no head-of-line blocking behind a large frame. We use TCP because (1) browsers cannot send raw UDP and (2) it shares the existing connection. We designed for loss anyway: the receiver expires the indicator after 4 s, and the server drops invalid TYPING silently |
| `PING` / `PONG` | Not needed | Tiny | Measures timing | – | **TCP**, deliberately: it measures the RTT a *chat message* actually experiences, including TCP. UDP would measure a purer network RTT but would need loss handling |
| Web files (HTML/CSS/JS) | Essential | Bulk | Page load | – | **TCP** (HTTP) |

**Security:** neither TCP nor UDP provides confidentiality or authentication. Our system has none
(no TLS, no passwords). It is suitable for a LAN demo only. Adding TLS (`wss://`) would run over TCP;
UDP would require DTLS.

**What TCP costs us**, i.e. what we would gain with UDP:
- the 3-way handshake (1 RTT) before the HTTP upgrade (another RTT) — paid once per session, measured in §6;
- head-of-line blocking: one lost segment delays every later message on that connection, including TYPING;
- per-connection kernel state on the server.

Setting `TCP_NODELAY` removes Nagle's batching delay, at the cost of more small packets (§7).

### 4.4 Client behaviour relevant to the protocol (`web/app.js`)

- **Reconnect with back-off** (1, 2, 4, 8, 8… s). On reconnect the client re-sends `LOGIN` and its profile.
- **PING every 4 s**; the RTT is displayed under the user's name.
- **Typing:** the client sends `true` at most every 2.5 s while typing, and `false` after 2 s idle or on send.
- **Conversation storage:** messages are stored per conversation in the page's memory, with unread
  badges. Replayed `HISTORY` is deduplicated by `seq`.

---

## 5. Packet capture and encapsulation analysis (R5)

**Capture setup.** Wireshark ran on Host A (the server, `192.168.100.126`) on its Wi-Fi interface. The
capture was started *before* Host B loaded the page, so it includes the HTTP upgrade and Wireshark can
decode WebSocket. File: `Wireshark.pcapng` (submitted with the source code). The analysed frame is
**packet 227**, captured 2026-11-15 12:07:19.0238 (Pakistan Standard Time), 15.645 s into the capture.
It is the server relaying a public CHAT message `"Hi"`, sent by user `Umerr` on Host B, to the online users.

![Figure 5.1: annotated Wireshark packet 227](figures/wireshark_packet227_annotated.png)

**Figure 5.1.** Packet 227 as captured on Host A, annotated layer by layer. The capture file is
`Wireshark.pcapng`.

### 5.1 Observed values for packet 227 (server `192.168.100.126` → client `192.168.100.50`)

| Layer (Lecture 03) | Field | Observed value | Size |
|---|---|---|---|
| Link (Ethernet II) | Destination MAC | `20:79:18:3a:e3:0f` (Intel adapter, Host B) | 14 B |
| | Source MAC | `78:be:81:05:50:10` (FN-Link adapter, Host A) | |
| | Type | `0x0800` (IPv4) | |
| Network (IPv4) | Version, header length | 4, 20 bytes | 20 B |
| | Total length | 145 (= 20 + 20 + 105) | |
| | Flags, TTL, protocol | Don't Fragment, **128**, **6 (TCP)** | |
| | Source, destination IP | **192.168.100.126**, **192.168.100.50** | |
| Transport (TCP) | Source, destination port | **5000**, **53586** (the browser's ephemeral port) | 20 B |
| | Flags | `0x018` = PSH, ACK | |
| | Seq / Ack (relative) | 1208 / 769 | |
| | Window | 252 × scale 256 = 64 512 B | |
| | Payload | 105 B | |
| Framing (WebSocket) | FIN, opcode, MASK | 1, 1 (text), **0** (server→client frames are not masked) | 2 B |
| | Payload length | 103 (header bytes `81 67`) | |
| Application (Mochi) | JSON | `{"type":"CHAT","username":"Umerr","text":"Hi","scope":"public","seq":22,"timestamp":1794726439.0235255}` | 103 B |
| **Whole frame** | | **14 + 20 + 20 + 2 + 103 = 159 B** (Wireshark: 159 bytes on wire) | **159 B** |

### 5.2 Encapsulation discussion

Each layer treats everything above it as opaque payload and adds its own header:

1. The server builds the JSON message (103 B).
2. `ws_framing.frame()` prepends the 2-byte WebSocket header. This is what lets the receiver find
   the message boundary in the TCP byte stream.
3. TCP (in the kernel) prepends a 20-byte header. The ports identify the server process (5000) and the
   browser's socket (53586). Sequence and acknowledgement numbers provide reliable, ordered delivery.
4. IP prepends 20 bytes. The IP addresses identify the two hosts, and TTL 128 is the Windows default.
5. The link layer prepends 14 bytes. The MAC addresses identify the adapters on this LAN.

Observations from the capture:

- **Mask = False.** Client→server frames carry a masking key, so their JSON would appear scrambled in
  Wireshark. Server→client frames, like this one, are readable.
- **PSH, ACK, with ACK = 769.** The server pushes the relayed message immediately (`TCP_NODELAY`) and
  piggybacks the acknowledgement of the client's earlier data on the same segment.
- **Overhead.** 56 of the 159 bytes (35 %) are headers, and the user's text `Hi` is 2 bytes, **1.3 %** of
  the frame. The JSON field names, username, `seq` and timestamp make up most of the remaining 101 B.
  This is the cost of choosing a readable text format (§7.3).
- **Checksums show as "unverified".** The sending adapter computes the checksums after Wireshark sees
  the packet (checksum offload). It is not an error.
- **Server-side delay, measured from the capture.** The JSON `timestamp` is set when the server builds
  the packet (`...439.0235255`). Wireshark stamped the frame leaving the server at `...439.0238455`.
  The difference is **about 0.32 ms**: JSON encoding, framing, the per-socket lock and the send call.
  This is the d_proc term of the delay model (§6.3), and it matches the ~0.3 ms measured on loopback.
  Because the capture is on the server, no network delay is included in it.

*Note on Wi-Fi captures on Windows:* without monitor mode, Npcap presents Wi-Fi traffic as Ethernet II.
The real 802.11 header (with the access point's address and Wi-Fi-specific fields) is not visible.

---

## 6. Performance measurement (R6)

### 6.1 Method

Tool: `mochi-v2/bench.py`, a raw-socket WebSocket client. Run it on **a different host from the
server**:

```
py bench.py <server-ip> 5000 5        # 5 repetitions per measurement; writes bench_results.csv
```

It opens two logged-in connections A and B and measures:

| Measurement | Definition | Inner samples |
|---|---|---|
| RTT | PING→PONG on an open connection | mean of 20 per run |
| New-connection time | TCP connect + HTTP upgrade + LOGIN→LOGIN_OK | 1 per run |
| Response time of one operation | Private CHAT, 100 B: A sends → server → B receives | mean of 20 per run |
| Throughput, small messages | 200 × 50 B chat payloads A→server→B, KB/s | 1 per run |
| Throughput, large messages | 200 × 1000 B chat payloads | 1 per run |

Each measurement is repeated **5 times**; mean, standard deviation, min and max are reported.

**Conditions compared** (≥ 2 required; ours are built into the tool):
1. **Persistent vs new connection** (RTT on an open connection vs new-connection time).
2. **Small vs large messages** (50 B vs 1000 B throughput).
3. **Idle vs loaded Wi-Fi** (no wired link was available).
   - *Idle:* only the server and the benchmark host are using the access point.
   - *Loaded:* a third device on the same access point streams 4K video or downloads a large file
     during the whole benchmark run.

   This condition isolates the **queueing delay** term of the nodal delay model: the link, distance
   and server are identical, and only the competing traffic changes. Save the CSVs as `idle.csv` and
   `loaded.csv`.
4. *(Optional)* **Server logging on vs `--no-log`**, which isolates the server's **processing delay**.

### 6.2 Results

Raw data: `idle.csv` and `loaded.csv` (`bench.py` output, 5 runs per measurement; each RTT and relay
value is itself the mean of 20 samples). Client: Host B (`192.168.100.50`, Windows 11) ran `bench.py`
against the server on Host A (`192.168.100.126`); both were on the house Wi-Fi, which other people also use.
Host A reports 2.4 GHz, channel 1, 802.11g, 48 Mb/s link rate (`netsh wlan show interfaces`, checked after
the runs; Host B's band was not recorded). Run times, taken from the server log (the benchmark's own sessions `a<pid>`, `b<pid>`
and `tmp<pid>_<n>`):

| Condition | Started | Finished | Server-log session |
|---|---|---|---|
| Idle | 2026-11-15 12:33:59 | 12:34:07 | `a19776`, `b19776` |
| Loaded | 2026-11-15 12:37:22 | 12:37:31 | `a18796`, `b18796` |

The idle run came first and the loaded run 3 min 15 s after it, so the order was not randomised.
*Idle* means no extra load from us: other people in the house were using their phones on the same
Wi-Fi, so the network was not empty. *Loaded*: we started a YouTube video for this run (the device it
played on and its exact start time were not recorded).

| Measurement | Unit | Idle Wi-Fi: mean ± sd | Loaded Wi-Fi: mean ± sd | Idle min–max | Loaded min–max |
|---|---|---|---|---|---|
| RTT PING→PONG | ms | 7.44 ± 0.53 | 6.71 ± 2.64 | 6.69–8.15 | 4.32–10.86 |
| New connection (TCP + upgrade + LOGIN_OK) | ms | 49.01 ± 12.61 | 21.70 ± 7.33 | 30.11–65.18 | 13.97–31.94 |
| Chat relay, 100 B | ms | 14.11 ± 5.39 | 22.90 ± 22.61 | 8.21–20.05 | 4.91–51.40 |
| Throughput, 50 B messages | KB/s | 28.26 ± 2.46 | 24.01 ± 12.08 | 25.51–30.28 | 10.09–41.26 |
| Throughput, 1000 B messages | KB/s | 291.60 ± 37.73 | 312.64 ± 59.83 | 228.79–320.42 | 240.83–387.74 |

*Baseline only, not valid for R6 (loopback, single machine, 2026-10-31):* RTT 0.28 ± 0.02 ms;
new connection 1.61 ± 0.21 ms; relay 0.37 ± 0.02 ms; throughput 178 ± 20 KB/s (50 B) and
2719 ± 408 KB/s (1000 B). It shows the server's own processing cost with propagation and
transmission close to zero, and is used as the d_proc reference in §6.3.

![Figure 6.1: idle vs loaded Wi-Fi (mean, standard deviation, individual runs)](r6_results.png)

**Figure 6.1.** Bars are means over 5 runs, error bars are one standard deviation, dots are the
individual runs. Generated by `plot.py` (Appendix B).

### 6.3 Analysis with the nodal delay model (Lecture 02)

d_nodal = d_proc + d_queue + d_trans + d_prop, applied at each hop. Both laptops were on Wi-Fi, so
every packet crosses the air twice (client→AP, AP→server) and a round trip crosses it four times.

| Component | Estimate on our network | Expected impact |
|---|---|---|
| d_prop | ~10–50 m at ~2–3×10⁸ m/s ≈ **0.05–0.2 µs** | Negligible |
| d_trans | 159 B frame (measured, §5): at 100 Mb/s ≈ 12.7 µs; at a 50 Mb/s Wi-Fi rate ≈ 25 µs. A 1000-B message ≈ 1 056 B ≈ 85–170 µs | Small; grows linearly with message size |
| d_proc | Python: JSON parse/encode, lock, log line, syscalls. About **0.3 ms** per message (loopback benchmark, and 0.32 ms measured in the capture, §5) | Small but constant |
| d_queue | Wi-Fi channel access (CSMA/CA back-off), AP buffering, retransmissions | **The unpredictable part**; expected to grow when others share the channel |

**What the numbers show**

- **Wi-Fi vs loopback.** The RTT rises from 0.28 ms to 7.4 ms (about 26×). d_prop and d_trans are
  microseconds and d_proc is ~0.3 ms, so roughly **7 ms is spent in the wireless hops**: waiting for
  the channel and for the access point to forward each frame, four times per round trip.
- **Idle vs loaded: the spread changed, the averages mostly did not.**
  - The standard deviation grew in four of five measurements: RTT 0.53 → 2.64 ms (5×), relay
    5.4 → 22.6 ms (4×), 50 B throughput 2.5 → 12.1 KB/s (5×), 1000 B throughput 38 → 60 KB/s (1.6×).
  - The loaded **relay** runs split into two groups: three at 4.9–8.2 ms (about the RTT) and two at
    43–51 ms. Each run averages 20 relays, so the slow runs were slow for about a second: a queue built
    up behind the competing traffic and then drained.
  - The loaded **mean RTT was not higher** (6.71 vs 7.44 ms); its range (4.3–10.9 ms) simply contains
    both the fastest and the slowest run of the pair.
  - So in this experiment the competing traffic showed up as **unpredictable delay (d_queue)**, not as a
    uniformly higher delay. With 5 runs and these spreads, we cannot claim that the means differ,
    except for the new-connection result below.
- **New connection: opposite to the prediction.** Setup took 49.0 ms idle but 21.7 ms loaded.
  - The model predicts 3 round trips (TCP handshake, HTTP upgrade, LOGIN→LOGIN_OK) plus processing.
    The loaded run fits it: 21.7 ms ≈ 3.2 × its 6.71 ms RTT.
  - The idle run took 6.6 × its RTT, which is more than the model explains. The two ranges barely
    overlap (30–65 ms vs 14–32 ms), so the difference is probably real.
  - **We did not identify the cause.** Possibilities we could not test: other household traffic during
    the "idle" run (the network is shared with other people, so "idle" is only relatively idle), or Wi-Fi
    power saving delaying packets on a quiet link. Testing them needs repeated runs on a private network.
- **Small vs large messages.**
  - 50 B messages: 28.3 KB/s ≈ 578 msg/s ≈ **1.7 ms per message**. 1000 B messages: 291.6 KB/s ≈ 298
    msg/s ≈ **3.4 ms per message**.
  - The message is 20× larger but costs only 1.9× more, so payload throughput is **10× higher**. A fixed
    per-message cost (processing plus one Wi-Fi channel access per packet) dominates, as on loopback
    (0.27 vs 0.36 ms per message, a 15× throughput gap).
  - On Wi-Fi the gap **shrank** from 15× to 10× and the per-message cost is 6–9× higher than on
    loopback, because airtime for the bigger frame now matters (d_trans and d_queue).
- **Relay vs RTT (correction to our first estimate).** We expected relay ≈ RTT + d_proc. The idle relay
  is 14.1 ms, about 1.9 × the RTT. The benchmark's relay measurement sends one message from A and waits
  for **two deliveries** (to B, then A's own echo), and both connections belong to the same laptop, so it
  crosses the air once up and twice down: roughly 1.5 × RTT + d_proc ≈ 11–12 ms, close to what we see.

**Limits of this experiment.** One shared home network, not a controlled lab; 5 runs per condition; one
client laptop running both benchmark connections; idle run first and loaded run 3 min 15 s later, not in
random order. These results
describe our setup and should not be generalised.

---

## 7. Design tensions and engineering justification (Section 6)

*(Where a figure below is marked "loopback", it is from the single-machine baseline. Wi-Fi figures are
from §6.2.)*

### 7.1 State vs simplicity: we keep state on the server, in RAM

**Position:** the server is stateful. It holds sessions, profiles, groups, the last 500 messages and
the `seq` counter in memory, with no database.

**Benefits:**
- Presence, history replay, group membership and global ordering need a single authority. A stateless
  relay could do none of these.
- Keeping it in RAM keeps the code small and fast (no disk I/O inside the lock).

**Costs, observed or found by inspection:**
- A server restart loses all groups, history and profiles, and `seq` restarts at 1.
- Clients that stay open keep their old conversations in memory, so **client and server state
  diverge**. The client deduplicates history by `seq`, so after a restart a new message whose `seq`
  equals an old one could be skipped during a history replay, and sorting by `seq` would place new
  messages before old ones.
- Fixes: a per-server-run epoch in LOGIN_OK so clients reset their dedupe set, or persistent storage.
  We judged them out of scope and document the limitation.
- **[TODO: optional: demonstrate by restarting the server with a tab open and screenshot.]**

### 7.2 Connection reuse vs server resources: one persistent connection per client

**Position:** each client keeps one TCP/WebSocket connection open for the whole session.

**Evidence:** connection setup costs **49.0 ms (idle) / 21.7 ms (loaded)** against an RTT of **7.4 / 6.7 ms**
on an open connection (§6.2; loopback: 1.61 vs 0.28 ms). In bytes, setting
up a session costs ≈ 1.4 KB against ≈ 159 B for one message on an open connection (§8).

**Cost:**
- the server holds one socket, one thread (with its own stack) and kernel TCP buffers per online user,
  even while that user is idle;
- the clients send a PING every 4 s to keep the connection observably alive.

For a class-sized deployment (tens of users) this is cheap. For thousands of users, threads would be
replaced by `select`/`selectors`-based I/O.

### 7.3 Compactness vs readability: JSON text, not a binary format

**Position:** JSON in text frames.

**Benefits:**
- Readable in Wireshark and in our logs, which made debugging and the R5/R7 evidence straightforward.
- Trivial to parse in both Python and JavaScript.
- Easy for another group to implement from the spec.

**Measured cost (from our log):**
- The relayed CHAT "Hi" captured in Wireshark (§5) is **103 B** of JSON for 2 B of text.
- A binary layout (type 1 B, scope 1 B, seq 4 B, timestamp 8 B, length-prefixed username 1+5 B,
  length-prefixed text 2+2 B) would be **24 B**: 77 % smaller at the application layer.
- Counting Ethernet + IPv4 + TCP + WebSocket headers (56 B), the frame shrinks from 159 B to 80 B,
  **50 % smaller**.
- For a 1000-character message, JSON adds ~99 B to ~1056 B (< 10 %).
- So the overhead matters only for short messages, PRESENCE and TYPING. We kept readability.

### 7.4 Reliability vs latency: TCP everywhere, Nagle disabled

**Position:**
- TCP for every message type, including advisory TYPING (§4.3).
- `TCP_NODELAY` is set so small frames are sent immediately rather than batched while waiting for an
  ACK. Nagle + delayed ACK can add tens of ms (up to ~200 ms) per message.

**Cost:**
- more, smaller packets (higher header overhead, §8);
- head-of-line blocking of TYPING behind a large frame, e.g. a USER_LIST carrying avatars.

**Evidence:** **[TODO: optional: run bench with TCP_NODELAY off and compare the relay time.]**

### 7.5 (Supporting) Ordering vs isolation

The single global lock gives total ordering at the cost of letting one slow receiver stall
broadcasts. Mention this briefly if space allows.

---

## 8. Sustainability and efficiency analysis (Section 7)

**Message sizes from our log.**

| Message | Payload |
|---|---|
| CHAT "Hi" (user `Umerr`, `seq` 22; captured in Wireshark) | 103 B |
| PING | 39 B |
| PONG | 72 B |
| PRESENCE | 92–94 B |
| USER_LIST (1 user, no avatar) | 95 B |
| TYPING | 51 B |

**Per-packet overhead:**
- Ethernet + IPv4 + TCP = 54 B per packet;
- plus a 2 B WebSocket header from the server, or 6 B from the client (mask).

Header sizes confirmed in the capture (§5.1): Ethernet 14 B, IPv4 20 B, TCP 20 B with **no options** (no
timestamps), so 54 B per data packet. The CHAT frame in the capture is **159 B**: 54 + 2 + 103 B of JSON.

**(a) Connection reuse.** Setting up a new session costs about **1.4 KB**:

| Part | Bytes |
|---|---|
| Upgrade request | ~156 B |
| 101 response | 129 B |
| LOGIN | 59 B |
| LOGIN_OK / PRESENCE / USER_LIST / GROUP_LIST | ~300 B |
| Packet headers (~14 packets incl. handshake and teardown) | ~780 B |

Sending a message on an already-open connection costs about **159 B**. A design that reconnected per
message would use ~9× the bytes, so **reuse saves ≈ 89 %** per message.

**(b) Format.** A compact binary CHAT saves 79 B per delivery (50 % per packet, §7.3).

Illustrative population: a lobby of 200 online students, each sending 50 short messages a day.
Every public message is delivered to all 200 users, so 200 × 50 × 200 = **2 million deliveries/day**.

| Format | Arithmetic | Per day |
|---|---|---|
| JSON | 2 M × 159 B | ≈ 318 MB |
| Binary | 2 M × 80 B | ≈ 160 MB |

So the binary format saves ≈ 158 MB/day.

**(c) Keep-alive is the hidden cost.** Each 4-second PING cycle costs:
- PING: 39 + 6 + 54 = 99 B;
- PONG: 72 + 2 + 54 = 128 B;
- total ≈ 227 B, i.e. ≈ 57 B/s ≈ **204 KB per hour per client**.

For the same 200 users over 8 hours that is ≈ **327 MB/day**: *more than all the chat traffic*.
Raising the interval to 15 s cuts this by 73 % (≈ 87 MB/day). The server does not rely on PINGs to
detect dead clients (TCP does that), so the only loss is a less frequently updated RTT display.

**(d) Avatars scale quadratically.** USER_LIST carries every user's avatar to every user on every
join, leave or profile change. With N users and avatars of size *s*, one event costs N²·s bytes. For
N = 30 and s ≈ 7 KB, that is ≈ 6.3 MB per join. **[TODO: read a real USER_LIST byte count with
avatars from `logs/server.log` and recompute.]** Sending avatars only when they change would remove
most of this.

**Implications.**
- Fewer bytes means less radio airtime. On Wi-Fi, transmitting is the costly state for the
  client's battery, so cutting keep-alive traffic and avatar re-sends directly reduces client energy use.
- It also frees shared airtime, reducing d_queue for everyone on the same access point.
- Of the four options, the cheapest change (the PING interval) also gives the largest saving.

---

## 9. Behaviour under failure (R7)

### 9.1 Client disconnects mid-session (demonstrated)

**Designed behaviour:**
1. When a client's socket closes, cleanly or abruptly, its server thread leaves the receive loop.
2. `cleanup()` removes the session under the lock.
3. The server broadcasts `PRESENCE LEFT` with a new `seq`, then a fresh `USER_LIST`.
4. The client's name becomes free again immediately.

**Evidence** (`logs/server.log`, two hosts, 2026-11-14):

```
11:48:49.630 CHAT seq=7 ahmed -> public (18 chars, 2 recipients)
11:48:56.096 192.168.100.50:49845 closed (peer closed)
11:48:56.096 LEFT Umerr
11:48:56.099 SEND ahmed 92B {"type":"PRESENCE","event":"LEFT","username":"Umerr","seq":8,...}
```

The remaining user was notified 3 ms after the disconnect was detected.

**Limitation:** if a cable is pulled or Wi-Fi drops without a FIN or RST, the server only notices when
a later send fails or TCP keepalive fires. That takes minutes with OS defaults, so the user appears
online until then.

### 9.2 Silent connection timeout (demonstrated)

**Designed behaviour:** a new connection must send its HTTP request within 30 s (`HTTP_TIMEOUT`) or
it is dropped. This stops idle or half-open connections from holding server threads.

**Evidence:**

```
11:48:32.074 192.168.100.50:56436 closed (timed out)
```

The browser on Host B had opened a speculative extra TCP connection that never carried a request. The
server released it after 30 s, and the real session (port 49845) was unaffected.

### 9.3 Conflicting simultaneous requests

- **Two logins with the same name:** the check and the insert run under one lock, so exactly one
  succeeds and the other gets `ERROR USERNAME_TAKEN` (the comparison is case-insensitive).
- **Simultaneous chat messages:** these receive distinct `seq` values and appear in the same order on
  every client (§2.6).

Both behaviours were verified by our raw-socket protocol test (33 checks, all passing).
**[TODO: for the report, show it live: two hosts log in as the same name at the same time; screenshot
the error and the log.]**

### 9.4 Server unreachable at client start / server restart

**Designed behaviour:**
1. The client shows "offline · retrying…" and reconnects with back-off at 1, 2, 4, 8, 8… s.
2. When the server is back, the client re-sends LOGIN and its profile automatically.
3. If the old session is still registered, the server answers `USERNAME_TAKEN` and the client retries later.

**[TODO: evidence: stop the server, open the page/keep a tab open, start the server after ~15 s;
screenshot the status line and the server log showing the automatic re-login.]**

---

## 10. Individual contributions

| Member (roll no.) | Implementation | Report / evidence | Signature |
|---|---|---|---|
| **[TODO]** | | | |
| **[TODO]** | | | |
| **[TODO]** | | | |
| **[TODO]** | | | |

---

## 11. References

1. J. F. Kurose and K. W. Ross, *Computer Networking: A Top-Down Approach*, 8th ed., Pearson, 2021.
2. I. Fette and A. Melnikov, "The WebSocket Protocol," RFC 6455, IETF, Dec. 2011.
3. W. Eddy, Ed., "Transmission Control Protocol (TCP)," RFC 9293, IETF, Aug. 2022.
4. T. Bray, Ed., "The JavaScript Object Notation (JSON) Data Interchange Format," RFC 8259, IETF, Dec. 2017.
5. R. Fielding, M. Nottingham and J. Reschke, Eds., "HTTP Semantics," RFC 9110, IETF, Jun. 2022.
6. Python Software Foundation, "socket — Low-level networking interface," Python 3 documentation.
7. Wireshark Foundation, *Wireshark User's Guide*.
8. CS-327 lecture notes: Lecture 02 (delay model), Lecture 03 (encapsulation), Lecture 04 (transport service requirements).

---

## Appendix A: Source code

Repository / archive: **[TODO: link]**. Submitted version: `mochi-v2/`.

| File | Responsibility |
|---|---|
| `backend/main.py` | Command-line options, listening socket, accept loop |
| `backend/config.py` | All settings and limits |
| `backend/connection.py` | One connection's life: HTTP or WebSocket, receive loop, cleanup |
| `backend/http_static.py` | HTTP request parsing, static file responses |
| `backend/ws_framing.py` | WebSocket accept key, frame encode/decode |
| `backend/state.py` | Shared state and the global lock |
| `backend/outbox.py` | send / emit / error |
| `backend/broadcast.py` | USER_LIST, GROUP_LIST, PRESENCE, HISTORY |
| `backend/handlers.py` | Per-message-type protocol logic |
| `backend/logger.py` | Millisecond-timestamped logging |
| `web/index.html`, `style.css`, `app.js` | Browser client |
| `bench.py` | Raw-socket benchmark client (R6) |

**[TODO: attach the code listing as required by the course.]**

## Appendix B: Plot script for §6

```python
"""R6 graph from bench.py CSV files.   py plot.py idle.csv loaded.csv   ->  r6_results.png

One panel per measurement, one bar per condition (mean), error bar = standard deviation,
dots = the individual runs. matplotlib is only a plotting helper (allowed under R2).
"""
import csv
import sys

import matplotlib.pyplot as plt

SHORT = {  # panel titles; keys are the measurement names bench.py writes
    "RTT, PING->PONG on open connection": "RTT (PING→PONG)",
    "new connection (TCP+upgrade+LOGIN_OK)": "New connection",
    "chat relay A->server->B, 100 B": "Chat relay, 100 B",
    "throughput, 200 x 50 B messages": "Throughput, 50 B msgs",
    "throughput, 200 x 1000 B messages": "Throughput, 1000 B msgs",
}


def load(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return {
        r["measurement"]: {
            "mean": float(r["mean"]),
            "sd": float(r["stdev"]),
            "unit": r["unit"],
            "runs": [float(r[k]) for k in r if k.startswith("run")],
        }
        for r in rows
    }


files = sys.argv[1:]
if len(files) < 2:
    sys.exit("usage: py plot.py idle.csv loaded.csv")
names = [p.rsplit(".", 1)[0].replace("_", " ") for p in files]
data = [load(p) for p in files]
measurements = list(data[0])

fills = ["#9ecae1", "#2b6cb0"]   # light vs dark: still distinguishable in a black-and-white print
hatches = ["", "//"]
fig, axes = plt.subplots(1, len(measurements), figsize=(3 * len(measurements), 3.6))
for ax, m in zip(axes, measurements):
    for i, d in enumerate(data):
        s = d[m]
        ax.bar(i, s["mean"], yerr=s["sd"], capsize=5, color=fills[i % 2], hatch=hatches[i % 2],
               edgecolor="black", linewidth=0.8)
        ax.scatter([i + (j - 2) * 0.06 for j in range(len(s["runs"]))], s["runs"],
                   s=16, color="black", edgecolors="white", linewidths=0.6, zorder=3)
    ax.set_xticks(range(len(data)), names)
    ax.set_title(SHORT.get(m, m), fontsize=10)
    ax.set_ylabel(data[0][m]["unit"])
    ax.spines[["top", "right"]].set_visible(False)
fig.suptitle("bar = mean of 5 runs, error bar = standard deviation, dots = individual runs", fontsize=9)
fig.tight_layout()
fig.savefig("r6_results.png", dpi=200)
print("saved r6_results.png")
```

## Appendix C: AI usage declaration

**Tool.** Claude (Anthropic), models Claude Opus 5.5 and Claude Sonnet 5.5, used through Claude Code in
VS Code between 2026-10-31 and 2026-11-15.

**What the AI did not do.** It produced no measurements. Every number in §5 (packet capture), §6
(benchmark), §8 (message sizes) and §9 (failure evidence) comes from the group's own Wireshark capture,
benchmark CSV files and server log, collected on the group's three laptops. The AI wrote the text
around those numbers and did the arithmetic on them.

**How the AI-written code was checked.**
- 33 raw-socket protocol tests covering every message type and every error code.
- After the readability edits, a comparison of the code against the version before the edit, ignoring
  comments and docstrings, to show that behaviour was unchanged.
- The original `bench.py`, run unchanged against the modular server.
- Real use on three laptops (Hosts A, B and C, §3).

All AI output was reviewed by the group. Every member must be able to explain all of it in the demo.

| Date | Part of the project | What the AI produced | What the group did |
|---|---|---|---|
| 2026-10-31 | Backend modularisation and new UI (`mochi-v2/`) | Split of the single-file server into 10 modules; logging and command-line options; new HTML/CSS and an adapted `app.js`; `PROCESS.md`; a raw-socket protocol test script | Set the scope: keep the original code untouched, put the new version in its own folder with `backend/` and `web/`, and replace the old look with a neutral UI. Reviewed the modules and ran the system on two laptops |
| 2026-11-14 | Code readability | Rewrote `broadcast.py` and `state.py` so they can be read and explained; added comments to `handlers.py` and replaced short variable names with descriptive ones (behaviour checked unchanged by the protocol test) | Asked for each file to be made readable, read each file as it was explained, and trimmed comments and docstrings in `broadcast.py`, `handlers.py` and `outbox.py` |
| 2026-11-14 to 2026-11-15 | Repository layout and report text | Moved the original version into `mochi-v1/`; drafted `report.md`: protocol specification from the code (§2), transport choice per message type (§4.3), design tensions (§7), sustainability arithmetic (§8) | Supplied the logs, captures and results the text is based on; filled in the benchmark conditions; reviewed the draft |
| 2026-11-15 | Evidence figures and benchmark analysis | Network diagram (`network_diagram.drawio` and PNG), annotated Wireshark figure and combined `ipconfig` figure (generated by script from the group's own screenshots); `plot.py` and the R6 graph; the analysis text in §5, §6.3 and §8; the run-time table in §6.2, derived from `server.log` | Ran the benchmark on Host B (two runs), captured packet 227 on Host A, took the `ipconfig` screenshots, and set up the three-host network |
| 2026-10-31 to 2026-11-15 | Explanations | Walkthroughs of the brief, the protocol, the server code and networking concepts (WebSocket framing, TCP behaviour, Wireshark output) | Used for understanding; not copied into the report |
