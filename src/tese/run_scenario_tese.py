"""Start the network topology for the master's thesis experiment
(static PQC vs. adaptive PQC protocol selection).

This script only brings the network up (router, switches, endpoint
placeholders) and, optionally, starts packet capture on the two
client/server links so traffic can be inspected later (e.g. to measure
handshake time). It does not run any benchmark or cryptography -- that
belongs to the next step, once the PQC client/server image and the
adaptive-selection model are implemented (see README.md).

Run from the `src/tese` directory, with the GNS3 server running and after
create_topology_tese.py has been run at least once:
    (venv) $ python3 run_scenario_tese.py
"""

import json
import re
import sys
import time

from pathlib import Path

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gns3utils import *

PROJECT_NAME = "tese_pqc"
STATE_FILE = Path("topology_state.json")

# Set to True to start Wireshark-style packet capture on the
# switch<->client and switch<->server links for this run.
START_CAPTURE = True

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
    print(f"State file {STATE_FILE} not found. Run create_topology_tese.py first.")
    sys.exit(1)

with open(STATE_FILE, "r", encoding="utf-8") as f:
    state = json.load(f)

nodes = state["nodes"]

check_ipaddrs(server, project)

#######
# Run #
#######

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

if START_CAPTURE:
    links_client = get_links_id_from_node_connected_to_name_regexp(
        server, project, nodes["switch_client"]["node_id"], re.compile(re.escape(nodes["client"]["name"])))
    links_server = get_links_id_from_node_connected_to_name_regexp(
        server, project, nodes["switch_server"]["node_id"], re.compile(re.escape(nodes["server"]["name"])))
    start_capture(server, project, [lk.id for lk in links_client])
    start_capture(server, project, [lk.id for lk in links_server])

print("\nNetwork is up: pqc-client (192.168.101.10) <-> pqc-router <-> pqc-server (192.168.102.10)")
print("Reminder: the endpoint nodes are still the generic debug-client placeholder. "
      "No cryptography is running -- that is the next step.")

##############################################################
# Stop the scenario: stop packet capturing and all the nodes #
##############################################################

# if START_CAPTURE:
#     stop_capture(server, project, [lk.id for lk in links_client])
#     stop_capture(server, project, [lk.id for lk in links_server])
#
# for role in ("client", "server", "switch_client", "switch_server", "router"):
#     print(f"Stopping {nodes[role]['name']}")
#     stop_node(server, project, nodes[role]["node_id"])
#     time.sleep(0.5)
