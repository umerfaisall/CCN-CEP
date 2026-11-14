# CLAUDE.md — CS-327 Computer Communication Networks, Term Project (Fall 2026)

**Project:** Design, Implementation and Evaluation of a Multi-Host Networked Application
**Context:** NED University (CSE dept.), Complex Engineering Problem. Max 4 students per group.

## 0. How to work with me on this project

- This is graded on **engineering reasoning, measured evidence, and honest evaluation**, not on matching a model answer. Always explain *why* a design choice was made and what it trades away.
- Every group member must be able to explain **any** part of the design in the live demo (Week 13). Explain code as you write it; keep it simple and readable over clever.
- **AI use must be declared** in a report appendix (what was used, for which part). Keep a running log in `docs/ai-usage-log.md`: date, task, what Claude produced, what the human changed.
- When I ask for planning/architecture, give **tradeoff-aware options with a recommendation and no code**. Only write code when I explicitly ask for implementation.
- Prefer tables for comparisons. For walkthroughs, tie each step to the actual message/packet/component involved.
- Ask me before assuming anything marked TBD below.

## 1. Project decisions (fill in / confirm)

| Item | Value |
|---|---|
| Selected option (A–E) | **TBD** |
| Language | **TBD** (Python / Java / C / C++) |
| Group members + roll numbers | **TBD** |
| Hosts (3+ independent IPs) | **TBD** (physical / VM bridged / mix) |
| Transport per message type | **TBD** (decide and justify, see R4) |

Options (pick exactly one):
- **A. Mini mail system**: submit, store in mailbox, retrieve from another host. Stronger: two mail servers relaying between domains.
- **B. Mini DNS**: authoritative server, caching resolver on a second host, clients on a third; show cache hit vs miss with TTL.
- **C. Web server + caching proxy**: status codes, persistent and non-persistent modes, measure page fetch time difference.
- **D. Multi-client chat with presence**: relay server, online list, join/leave notifications, handle message ordering under concurrent sends.
- **E. File distribution**: client-server vs simple P2P; measure total distribution time as receivers grow.

## 2. Mandatory requirements (all groups)

- **R1 Own protocol.** Spec must define: message types, exact format of every header field and its meaning, legal message order, action on receipt of each message, status/error codes. Written so another group could implement it without seeing our code. **No protocol spec is supplied; we invent it.**
- **R2 Raw sockets.** Client and server on the socket interface directly. **No library that implements the application protocol** (no SMTP lib for mail, no HTTP server framework for web, no DNS lib for DNS). Helper libs for files, plotting, etc. are fine.
- **R3 Multi-host.** At least **3 independent endpoints with distinct IPs** (physical, bridged/host-only VMs, or mix). **127.0.0.1 on one laptop is not acceptable** and is marked incomplete. Report needs `ipconfig`/`ip addr` output per host and a network diagram.
- **R4 Transport justification.** State TCP or UDP **per message type**. Justify against Lecture 04's four service requirements: reliable data transfer, throughput, timing, security. "TCP is reliable" earns zero. Explain what we would lose with the opposite choice.
- **R5 Wireshark capture.** One annotated packet on the link between two hosts showing: link-layer frame header, IP header (src/dst IP), transport header (src/dst ports), and our application message in the payload. Relate to Lecture 03 encapsulation.
- **R6 Performance.** Measure RTT between hosts, application-level throughput, and response/transfer time of one representative operation. **Each measurement repeated ≥5 times; report mean and spread.** Compare **≥2 conditions** (wired vs wireless, cached vs uncached, persistent vs new connection, small vs large messages). Provide a **table and ≥1 graph**, and explain using the Lecture 02 nodal delay model (processing + queueing + transmission + propagation).
- **R7 Failure behaviour.** Demonstrate ≥1 abnormal condition (server unreachable at client start, client disconnects mid-exchange, conflicting simultaneous requests, UDP datagram loss). Describe the **deliberately designed** behaviour and give **log or capture evidence** that it happened.

## 3. Design tensions (Section 6): resolve at least three

For each chosen tension: state the position taken and defend it **with our own measurements**.

- Reliability vs latency
- Connection reuse vs server resources
- State vs simplicity (does the protocol keep state? what did it cost, e.g. divergence after a crash?)
- Caching freshness vs traffic reduction (TTL)
- Compactness vs readability (binary vs text format)
- Security vs performance (only if we add auth/encryption)

## 4. Sustainability and efficiency (Section 7)

From the R6 data, estimate **bytes saved** by a design choice (cache hit vs miss, connection reuse vs repeated setup, compact vs verbose format). Express as a **percentage**, project to a realistic user population, and comment on bandwidth and energy implications. One paragraph of honest arithmetic from **our own numbers**, not generic green-computing claims.

## 5. Deliverables and deadlines

- **Proposal** (1 page, in person, end of Week 09): members and roll numbers, option, protocol outline, tools/languages, plan for 3 hosts, division of work.
- **Final report**: max **10 pages** (excluding rubric title page, references, code appendix).
- **Source code**: archive or repo link; must build and run on the demo machines.
- **Live demo (Week 13)**: system started on separate hosts; all members present.

### Required report structure (in order)
1. Introduction and option selected
2. Protocol specification (R1)
3. System and deployment architecture, network diagram, IP addressing plan (R3)
4. Implementation notes incl. transport justification (R2, R4)
5. Packet capture and encapsulation analysis (R5)
6. Performance measurement, results, graphs (R6)
7. Design tensions and engineering justification (Section 6)
8. Sustainability and efficiency analysis (Section 7)
9. Behaviour under failure (R7)
10. Individual contributions table, signed by all members
11. References
- Appendices: code, **AI usage declaration**

### Formatting
- Times New Roman, 12 pt body, 14 pt headings.
- Rubric attached on top as the title page (names and roll numbers filled in).
- Hard copy printed; PDF named `CS24XXX.pdf` (roll number, e.g. `CS24013.pdf`); each member uploads from their own Google Classroom account by end of Week 13.

## 6. Suggested repository layout

```
/
├── CLAUDE.md
├── README.md              # how to build/run on each host
├── docs/
│   ├── protocol-spec.md   # R1 spec, kept in sync with implementation
│   ├── deployment.md      # IPs, host configs, diagram source
│   ├── design-tensions.md # position + evidence per tension
│   ├── failure-tests.md   # R7 scenarios and expected behaviour
│   └── ai-usage-log.md    # for the AI declaration appendix
├── src/
│   ├── common/            # shared message encode/decode
│   ├── server/
│   └── client/
├── measurements/
│   ├── scripts/           # measurement harness
│   ├── raw/               # per-run data (>=5 runs per condition)
│   └── plots/
├── captures/              # .pcapng files + annotated screenshots
└── logs/                  # failure-demo logs
```

## 7. Working agreement for Claude Code

1. **Spec first.** `docs/protocol-spec.md` is the source of truth; code must match it. If they diverge, flag it and ask which changes.
2. **No protocol libraries** (R2). Before adding any dependency, state whether it implements any part of the application protocol.
3. **Never hardcode 127.0.0.1.** Hosts, ports, and addresses come from a config file or CLI args so the same build runs on all machines.
4. **Instrument from the start.** Timestamped logging of every message sent/received (needed for R6 and R7 evidence); a measurement harness that runs each condition ≥5 times and outputs raw data plus mean and standard deviation.
5. **Record tradeoffs as we go** in `docs/design-tensions.md` so the report section writes itself from real data.
6. **Keep the report budget in mind** (10 pages): flag anything that will not fit.
7. **Demo readiness:** maintain a one-command start script per host role and a checklist in `README.md`.

## 8. Open questions for the group

- Which option (A–E) best fits our available hosts and time? Compare effort, how naturally it yields the R6 comparison, and how easily it exposes three design tensions.
- Which transport per message type, and what is our measured evidence?
- Which three tensions will we defend, and which measurements back each?
- Who owns which component, so every member contributes to both implementation and report?