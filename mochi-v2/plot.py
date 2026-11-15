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
