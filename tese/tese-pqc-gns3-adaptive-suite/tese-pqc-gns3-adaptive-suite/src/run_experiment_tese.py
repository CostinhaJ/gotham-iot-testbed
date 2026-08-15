"""Sweep the rotation-interval x network-profile x device-profile matrix,
producing one ML-ready dataset for a future decision middleware (see
../tese/README.md; that middleware itself is out of scope here -- this
only collects the data it would train/evaluate against).

WHAT "ADAPTIVE" MEANS HERE (corrected design, see ../tese/README.md for
the full story): NOT switching which KEM/signature algorithm the
endpoints use -- that was an earlier iteration of this suite, and it
turned out to reintroduce the very overhead an adaptive scheme should
avoid (container restarts, per-algorithm certificates). The algorithm is
now FIXED for the whole run (whatever iotsim/pqc-static was built with,
see create_topology_adaptive_tese.py). The actual adaptive lever this
suite studies is: how often to redo the handshake and rotate the PQC
key, independently of which algorithm is in use. TLS 1.3 has no way to
re-run the KEM/signature without a full new handshake (its native
KeyUpdate only rotates symmetric traffic keys), so "rotate the key" and
"do another handshake" are the same operation here.

Loop order (outermost = most expensive to change):
    network profile (router reconfig + reboot, ~2 min)
      -> device profile (docker update, no restart, fast)
        -> rotation interval (just a sleep() duration -- no restart at all)

Each (network profile, device profile) combination is observed for a
fixed wall-clock OBSERVATION_WINDOW_S; within that window, the client
sleeps for the trial's rotation_interval_s, does one handshake, and
repeats until the window elapses (at least once, even if the interval is
longer than the window -- that's a valid data point too: "how much does
never rotating during this window cost/save"). This means the NUMBER of
handshakes per trial varies with the interval, by design: a 5s interval
over a 5-minute window yields ~60 rotations, a 300s interval yields 1.
That's what makes `trial_rotation_count` and the aggregated resource
metrics meaningful together -- total overhead over a realistic operating
window, as a function of interval, which is exactly what a future
middleware would trade off against key-exposure time.

Prerequisites: create_topology_adaptive_tese.py and
run_scenario_adaptive_tese.py must have already been run (GNS3 project
up, endpoints running).

    (venv) $ python3 run_experiment_tese.py [--observation-window-s N]
                                             [--rotation-intervals S [S ...]]
                                             [--network-profiles P [P ...]]
                                             [--device-profiles P [P ...]]
"""

import argparse
import csv
import json
import sys
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path

import docker

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from gns3utils import *

from capture_utils import (
    fetch_and_reset_keylog,
    get_capture_links,
    parse_trial_pcap,
    start_trial_capture,
    stop_trial_capture_and_download,
)
from device_profiles import DEVICE_PROFILES, apply_device_profile
from network_profiles import NETWORK_PROFILES, apply_network_profile
from resource_sampler import ResourceSampler

PROJECT_NAME = "tese_pqc_adaptive"
STATE_FILE = Path("../tese/topology_state.json")
RESULTS_DIR = Path("../tese/results")
CAPTURES_DIR = Path("../tese/results/captures")
CLIENT_KEYLOG_PATH = "/tmp/keylog.txt"
CA_CERT_PATH = "/certs/ca.crt"  # baked into iotsim/pqc-static, see that suite's Dockerfile

# How often to redo the handshake (rotate the key), in seconds. 300s with
# a 300s observation window (the default below) means that interval
# yields exactly one handshake for the whole window -- the "essentially
# no rotation" reference point.
DEFAULT_ROTATION_INTERVALS_S = [5, 15, 30, 60, 120, 300]
DEFAULT_OBSERVATION_WINDOW_S = 300

CSV_FIELDS = [
    "run_id", "trial_id", "suite_type", "kem_group", "sig_alg",
    "rotation_interval_s", "observation_window_s",
    "network_profile", "network_delay_ms", "network_loss_pct", "network_bandwidth_kbit",
    "device_profile", "device_cpu_limit", "device_mem_limit_mb",
    "rotation_index", "ok", "handshake_time_s",
    "trial_rotation_count",
    "trial_cpu_pct_mean", "trial_cpu_pct_max", "trial_mem_mb_mean", "trial_mem_mb_max",
    "trial_net_rx_bytes", "trial_net_tx_bytes",
    "trial_pcap_total_bytes", "trial_clienthello_bytes", "trial_serverhello_bytes",
    "trial_certificate_bytes", "trial_tcp_retransmits", "trial_capture_duration_s",
    "timestamp_utc",
]


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--observation-window-s", type=float, default=DEFAULT_OBSERVATION_WINDOW_S,
                         help=f"wall-clock duration of each trial (default: {DEFAULT_OBSERVATION_WINDOW_S}s)")
    parser.add_argument("--rotation-intervals", type=float, nargs="+", default=DEFAULT_ROTATION_INTERVALS_S,
                         help=f"candidate key-rotation intervals in seconds to sweep (default: {DEFAULT_ROTATION_INTERVALS_S})")
    parser.add_argument("--network-profiles", nargs="+", default=list(NETWORK_PROFILES), choices=list(NETWORK_PROFILES), help="subset of network profiles to sweep (default: all)")
    parser.add_argument("--device-profiles", nargs="+", default=list(DEVICE_PROFILES), choices=list(DEVICE_PROFILES), help="subset of device profiles to sweep (default: all)")
    parser.add_argument("--output", type=Path, default=None, help="output CSV path (default: ../tese/results/adaptive_sweep_<timestamp>.csv)")
    return parser.parse_args()


def run_one_handshake(client_container, server_host: str, server_port: int, kem_group: str):
    """Time a single TLS 1.3 handshake -- same host-side timing method as
    the static suite's run_benchmark_tese.py (docker exec + perf_counter,
    not timed inside the container's shell -- see that script's
    docstring for why). `-keylogfile` lets capture_utils.py decrypt
    Certificate-message sizes from the pcap later (openssl opens it in
    append mode, so it accumulates across a trial's rotations without any
    extra bookkeeping here)."""
    handshake_cmd = [
        "openssl", "s_client",
        "-connect", f"{server_host}:{server_port}",
        "-CAfile", CA_CERT_PATH,
        "-groups", kem_group,
        "-keylogfile", CLIENT_KEYLOG_PATH,
        "-quiet",
    ]
    t0 = time.perf_counter()
    exit_code, _ = client_container.exec_run(handshake_cmd)
    elapsed = time.perf_counter() - t0
    return exit_code == 0, elapsed


def run_trial(client_container, server_host: str, server_port: int, kem_group: str,
              rotation_interval_s: float, observation_window_s: float):
    """Run one (rotation_interval, network, device) trial: one warmup
    handshake (discarded), then repeated handshakes spaced by
    rotation_interval_s until observation_window_s has elapsed -- always
    at least one, even if the interval is longer than the window (see
    module docstring)."""
    run_one_handshake(client_container, server_host, server_port, kem_group)  # warmup, discarded

    rows = []
    start = time.monotonic()
    rotation_index = 0
    while True:
        time.sleep(rotation_interval_s)
        rotation_index += 1
        ok, elapsed = run_one_handshake(client_container, server_host, server_port, kem_group)
        print(f"  rotation {rotation_index} (t={time.monotonic() - start:.1f}s)  {'OK' if ok else 'FAIL'}  {elapsed * 1000:.2f} ms")
        rows.append({"rotation_index": rotation_index, "ok": ok, "handshake_time_s": round(elapsed, 6)})
        if time.monotonic() - start >= observation_window_s:
            break
    return rows


def main():
    args = parse_args()

    check_resources()
    check_local_gns3_config()
    server = Server(*read_local_gns3_config())
    check_server_version(server)

    project = get_project_by_name(server, PROJECT_NAME)
    if not project:
        print(f"Project {PROJECT_NAME} does not exist! Run create_topology_adaptive_tese.py first.")
        sys.exit(1)

    if not STATE_FILE.exists():
        print(f"State file {STATE_FILE} not found. Run create_topology_adaptive_tese.py first.")
        sys.exit(1)
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        state = json.load(f)
    nodes = state["nodes"]
    server_host = state["networks"]["server_zone"]["server_ip"].split("/")[0]
    server_port = state["server_port"]

    docker_client = docker.from_env()
    docker_client.ping()

    client_container_id = get_node_docker_container_id(server, project, nodes["client"]["node_id"])
    client_container = docker_client.containers.get(client_container_id)
    server_container_id = get_node_docker_container_id(server, project, nodes["server"]["node_id"])
    server_container = docker_client.containers.get(server_container_id)
    if client_container.status != "running":
        print(f"Client container is '{client_container.status}', not 'running'. Run run_scenario_adaptive_tese.py first.")
        sys.exit(1)

    # Fixed for the whole run -- resolved from the container's actual env
    # once, same reasoning as the static suite's run_benchmark_tese.py:
    # the CSV should reflect what's really running, not what a build-arg
    # said it would be.
    exit_code, env_out = client_container.exec_run(["sh", "-c", "echo \"$PQC_KEM_GROUP:$PQC_SIG_ALG\""])
    if exit_code != 0:
        print("Could not read PQC_KEM_GROUP/PQC_SIG_ALG from the client container.")
        sys.exit(1)
    kem_group, sig_alg = env_out.decode().strip().split(":")

    client_link_ids, server_link_ids = get_capture_links(server, project, state)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = args.output or (RESULTS_DIR / f"adaptive_sweep_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv")
    run_id = str(uuid.uuid4())

    total_trials = len(args.network_profiles) * len(args.device_profiles) * len(args.rotation_intervals)
    print(f"[experiment] run_id={run_id} algo={kem_group}/{sig_alg} trials={total_trials} "
          f"(rotation_intervals={args.rotation_intervals} x network={len(args.network_profiles)} x device={len(args.device_profiles)})")
    print(f"[experiment] each trial observed for {args.observation_window_s}s -- rotation count varies with interval")
    print(f"[experiment] writing rows to {out_path.resolve()} as each trial finishes")

    with open(out_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()

        current_network_profile = None
        for network_profile in args.network_profiles:
            if network_profile != current_network_profile:
                apply_network_profile(server, project, nodes["router"]["node_id"], network_profile)
                current_network_profile = network_profile
            net_meta = NETWORK_PROFILES[network_profile]

            for device_profile in args.device_profiles:
                apply_device_profile(client_container, device_profile)
                apply_device_profile(server_container, device_profile)
                dev_meta = DEVICE_PROFILES[device_profile]

                for rotation_interval_s in args.rotation_intervals:
                    trial_id = f"{rotation_interval_s}s_{network_profile}_{device_profile}"
                    print(f"\n[trial] {trial_id}")

                    client_container.exec_run(["rm", "-f", CLIENT_KEYLOG_PATH])
                    start_trial_capture(server, project, client_link_ids, server_link_ids)
                    sampler = ResourceSampler(docker_client, client_container)
                    sampler.start()

                    trial_rows = run_trial(client_container, server_host, server_port, kem_group,
                                            rotation_interval_s, args.observation_window_s)

                    resource_metrics = sampler.stop()
                    keylog_bytes = fetch_and_reset_keylog(client_container, CLIENT_KEYLOG_PATH)
                    client_pcap = stop_trial_capture_and_download(server, project, client_link_ids, server_link_ids, CAPTURES_DIR, trial_id)
                    pcap_metrics = parse_trial_pcap(client_pcap, keylog_bytes, CAPTURES_DIR, trial_id)

                    timestamp = datetime.now(timezone.utc).isoformat()
                    trial_rotation_count = len(trial_rows)
                    for row in trial_rows:
                        writer.writerow({
                            "run_id": run_id,
                            "trial_id": trial_id,
                            "suite_type": "adaptive_rotation",
                            "kem_group": kem_group,
                            "sig_alg": sig_alg,
                            "rotation_interval_s": rotation_interval_s,
                            "observation_window_s": args.observation_window_s,
                            "network_profile": network_profile,
                            "network_delay_ms": net_meta["delay_ms"],
                            "network_loss_pct": net_meta["loss_pct"],
                            "network_bandwidth_kbit": net_meta["bandwidth_kbit"],
                            "device_profile": device_profile,
                            "device_cpu_limit": dev_meta["cpus"],
                            "device_mem_limit_mb": dev_meta["mem_limit_mb"],
                            "rotation_index": row["rotation_index"],
                            "ok": row["ok"],
                            "handshake_time_s": row["handshake_time_s"],
                            "trial_rotation_count": trial_rotation_count,
                            **resource_metrics,
                            **pcap_metrics,
                            "timestamp_utc": timestamp,
                        })
                    csv_file.flush()

    print(f"\n[experiment] done. {total_trials} trials written to {out_path.resolve()}")


if __name__ == "__main__":
    main()
