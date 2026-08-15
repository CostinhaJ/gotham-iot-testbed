"""Start the network topology for the key-rotation-frequency PQC
experiment (thesis: cost of rotating PQC keys more/less often, algorithm
held fixed -- see create_topology_adaptive_tese.py for the full
rationale and why this reuses the static suite's image unchanged).

Mirrors ../../tese-pqc-gns3-static-suite/tese-pqc-gns3-static-suite/src/run_scenario_tese.py.
Only brings the network up (router, switches, endpoint placeholders); it
does not start packet capture here (unlike the static suite's
START_CAPTURE) because run_experiment_tese.py starts/stops a FRESH
capture per trial itself (see ../tese/README.md) -- a single
scenario-wide capture would blend every rotation-interval/network/device
condition into one unbounded pcap.

Run from the `src/` directory, with the GNS3 server running and after
create_topology_adaptive_tese.py has been run at least once:
    (venv) $ python3 run_scenario_adaptive_tese.py
"""

import json
import sys
import time

from pathlib import Path

# See create_templates_adaptive_tese.py's comment: gns3utils.py lives in
# <repo_root>/src, not on sys.path by default from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from gns3utils import *

PROJECT_NAME = "tese_pqc_adaptive"
STATE_FILE = Path("../tese/topology_state.json")

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())

check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)

if project:
    print(f"Project {PROJECT_NAME} exists. ", project)
else:
    print(f"Project {PROJECT_NAME} does not exist!")
    sys.exit(1)

open_project_if_closed(server, project)

if len(get_all_nodes(server, project)) == 0:
    print(f"Project {PROJECT_NAME} is empty!")
    sys.exit(1)

if not STATE_FILE.exists():
    print(f"State file {STATE_FILE} not found. Run create_topology_adaptive_tese.py first.")
    sys.exit(1)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

nodes = state["nodes"]

check_ipaddrs(server, project)

# 1. router first, it needs time to boot
print(f"Starting {nodes['router']['name']}")
start_node(server, project, nodes["router"]["node_id"])
time.sleep(60)

# 2. switches
for role in ("switch_client", "switch_server"):
    print(f"Starting {nodes[role]['name']}")
    start_node(server, project, nodes[role]["node_id"])
    time.sleep(1)

# 3. endpoints (client / server placeholders)
for role in ("client", "server"):
    print(f"Starting {nodes[role]['name']}")
    start_node(server, project, nodes[role]["node_id"])
    time.sleep(1)

print("\nNetwork is up: pqc-client (192.168.101.10) <-> pqc-router <-> pqc-server (192.168.102.10)")
print("Next: run_experiment_tese.py to sweep the rotation-interval x network x device matrix.")
