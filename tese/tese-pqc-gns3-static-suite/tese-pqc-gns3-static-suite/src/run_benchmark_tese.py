"""Benchmark the static PQC cipher suite over the thesis topology.

Runs N TLS 1.3 handshakes from pqc-client to pqc-server (both already
running -- see run_scenario_tese.py), using the single, fixed KEM group
and signature algorithm baked into the `iotsim-pqc-static` image at
build time (default: ML-KEM-768 / ML-DSA-65). Each handshake is a
separate `openssl s_client` call executed inside the client container
via `docker exec`, timed on the host side with time.perf_counter().

Why timing happens on the host and not inside the container's shell:
portable, sub-second timing across arbitrary /bin/sh implementations
(dash/ash/busybox all differ, and none is guaranteed to support bash's
`time` reserved word) is not reliable enough for a thesis measurement.
The trade-off is that each measurement also includes `docker exec`
process-spawn overhead on top of the raw network handshake -- see the
note printed at the end of this script and ../tese/README.md. Since the
adaptive model will be measured the same way, this overhead is at least
applied consistently across both conditions.

This produces the STATIC baseline. Comparing it against the adaptive
model's numbers (once that model is implemented and benchmarked the
same way) is what answers the thesis' research question.

Run from the `src/` directory, with the GNS3 server running and the
topology already started (run_scenario_tese.py):
    (venv) $ python3 run_benchmark_tese.py [--iterations N] [--warmup N]
"""

import argparse
import csv
import json
import sys
import time

from datetime import datetime, timezone
from pathlib import Path

import docker

from gns3utils import *

PROJECT_NAME = "tese_pqc"
STATE_FILE = Path("../tese/topology_state.json")
RESULTS_DIR = Path("../tese/results")
CA_CERT_PATH = "/certs/ca.crt"  # baked into the image, see Dockerfiles/pqc_static

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--iterations", type=int, default=50, help="number of TLS handshakes to time (default: 50)")
parser.add_argument("--warmup", type=int, default=3, help="handshakes to run and discard before timing starts (default: 3)")
args = parser.parse_args()

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())
check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)
if not project:
    print(f"Project {PROJECT_NAME} does not exist! Run create_topology_tese.py first.")
    sys.exit(1)

if not STATE_FILE.exists():
    print(f"State file {STATE_FILE} not found. Run create_topology_tese.py first.")
    sys.exit(1)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

server_host = state["networks"]["server_zone"]["server_ip"].split("/")[0]
server_port = state["server_port"]
client_node_id = state["nodes"]["client"]["node_id"]

client_container_id = get_node_docker_container_id(server, project, client_node_id)

docker_client = docker.from_env()
docker_client.ping()
client_container = docker_client.containers.get(client_container_id)
if client_container.status != "running":
    print(f"Client container is '{client_container.status}', not 'running'. Run run_scenario_tese.py first.")
    sys.exit(1)

# Resolve the group/signature actually baked into this container, so the
# CSV always reflects reality even if the image was rebuilt with
# different --build-arg values.
exit_code, env_out = client_container.exec_run(["sh", "-c", "echo \"$PQC_KEM_GROUP:$PQC_SIG_ALG\""])
if exit_code != 0:
    print("Could not read PQC_KEM_GROUP/PQC_SIG_ALG from the client container.")
    sys.exit(1)
kem_group, sig_alg = env_out.decode().strip().split(":")

handshake_cmd = [
    "openssl", "s_client",
    "-connect", f"{server_host}:{server_port}",
    "-CAfile", CA_CERT_PATH,
    "-groups", kem_group,
    "-quiet",
]


def run_one_handshake():
    """Run a single handshake inside the client container, return (ok, seconds)."""
    # exec_run does not attach stdin by default, which is equivalent to
    # `< /dev/null`: s_client performs the handshake and exits on EOF.
    t0 = time.perf_counter()
    exit_code, _ = client_container.exec_run(handshake_cmd)
    elapsed = time.perf_counter() - t0
    return exit_code == 0, elapsed


print(f"[bench] group={kem_group} sig={sig_alg} target={server_host}:{server_port}")
print(f"[bench] warmup: {args.warmup} handshake(s) (discarded)")
for _ in range(args.warmup):
    run_one_handshake()

print(f"[bench] timing {args.iterations} handshake(s)...")
rows = []
failures = 0
for i in range(1, args.iterations + 1):
    ok, elapsed = run_one_handshake()
    if not ok:
        failures += 1
    rows.append({
        "iteration": i,
        "ok": ok,
        "kem_group": kem_group,
        "sig_alg": sig_alg,
        "handshake_time_s": round(elapsed, 6),
    })
    print(f"  {i}/{args.iterations}  {'OK' if ok else 'FAIL'}  {elapsed * 1000:.2f} ms")

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out_path = RESULTS_DIR / f"static_{kem_group}_{sig_alg}_{timestamp}.csv"
with open(out_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["iteration", "ok", "kem_group", "sig_alg", "handshake_time_s"])
    writer.writeheader()
    writer.writerows(rows)

ok_times = [r["handshake_time_s"] for r in rows if r["ok"]]
if ok_times:
    mean = sum(ok_times) / len(ok_times)
    print(f"\n[bench] {len(ok_times)}/{args.iterations} OK, {failures} failed")
    print(f"[bench] mean={mean * 1000:.2f} ms  min={min(ok_times) * 1000:.2f} ms  max={max(ok_times) * 1000:.2f} ms")
    print("[bench] note: each measurement includes 'docker exec' process-spawn overhead on top of the "
          "raw network handshake -- see ../tese/README.md before quoting these as pure TLS handshake times.")
else:
    print("\n[bench] all handshakes failed -- check that pqc-server is running (run_scenario_tese.py) "
          "and that the router/switches are up.")
print(f"[bench] results written to {out_path.resolve()}")
