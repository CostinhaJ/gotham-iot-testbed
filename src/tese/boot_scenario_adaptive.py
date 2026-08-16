"""Start the End Node / Edge Node / Router / Server topology created by
create_topology_edge_tese.py.

Mirrors boot_scenario_adaptive.py (same suite, same idea: bring the router
up first since it needs real boot time, then the endpoints), just with the
switch-boot step removed (no switches in this architecture) and the new
node set (end_node, edge_node, server instead of client/server + 2 switches).

Does not start packet capture here, same reasoning as boot_scenario_adaptive.py:
a future experiment runner should start/stop capture per trial, not once for
the whole scenario.

Run from the `src/tese` directory, with the GNS3 server running and after
create_topology_edge_tese.py has been run at least once:
    (venv) $ python3 boot_scenario_edge.py
"""

import json
import sys
import time

from pathlib import Path

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gns3utils import *

PROJECT_NAME = "tese_pqc_edge"
STATE_FILE = Path("topology_state.json")

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
    print(f"State file {STATE_FILE} not found. Run create_topology_edge_tese.py first.")
    sys.exit(1)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

nodes = state["nodes"]

# Now actually meaningful with the check_ipaddrs() fix in gns3utils.py --
# it used to only check the FIRST address in a node's /etc/network/interfaces,
# which would have silently skipped one of the Edge Node's two addresses.
check_ipaddrs(server, project)

# 1. router first, it needs time to boot
print(f"Starting {nodes['router']['name']}")
start_node(server, project, nodes["router"]["node_id"])
time.sleep(60)

# 2. endpoints -- no switches to start in between anymore
for role in ("end_node", "edge_node", "server"):
    print(f"Starting {nodes[role]['name']}")
    start_node(server, project, nodes[role]["node_id"])
    time.sleep(1)

print("\nNetwork is up: end-node (192.168.100.10) <-> edge-node (192.168.100.1 / 192.168.101.10) "
      "<-> pqc-router <-> pqc-server (192.168.102.10)")
print("Next: validate connectivity before running any experiment script -- from inside each container:")
print("  end-node:  ping -c3 192.168.100.1     (its Edge Node neighbour)")
print("  edge-node: ping -c3 192.168.100.10 (End Node) and ping -c3 192.168.101.1 (Router)")
print("  server:    ping -c3 192.168.102.1     (Router)")
print("  edge-node -> server, through the router: ping -c3 192.168.102.10")
print("Once that's all clean, run_minimal_experiment.py needs updating for the new node names")
print("(end_node/edge_node/server) before it'll work against this topology -- it still assumes the")
print("old client/server + switch layout.")