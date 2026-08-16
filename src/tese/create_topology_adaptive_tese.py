"""Create the network topology for the End Node / Edge Node / Router /
Server architecture (replaces the switch-based topology from
create_topology_adaptive_tese.py -- see the chat history for why: with
Open vSwitch nodes in between, ARP requests from both endpoints came back
"incomplete" on both segments, i.e. frames were never actually being
switched. Point-to-point links have no such intermediate device to fail).

WHAT CHANGED, AND WHY (architecture):
  End Node (sensor)  --  Edge Node  --  Router (VyOS)  --  Server
  This is the standard "device / edge / cloud" reference architecture used
  in IoT + edge-computing literature (perception layer -> edge/fog layer ->
  application/cloud layer). Mapped onto what this suite already had:
    - Server: unchanged, same role, same zone (192.168.102.0/24).
    - Router: unchanged, same VyOS appliance, same two zone addresses
      (192.168.101.0/24 towards the edge, 192.168.102.0/24 towards the
      server) -- router/network_profiles/clean.sh needs NO changes.
    - Edge Node: takes over the old "pqc-client" role and address
      (192.168.101.10) on its Router-facing NIC. THIS is where the TLS/PQC
      handshake happens ("handshake decidido no Edge Node") -- matches the
      common PQC-at-the-edge pattern where a resource-constrained sensor
      can't run ML-KEM/ML-DSA itself, so an edge gateway does it on its
      behalf. Needs a SECOND NIC to also face the End Node.
    - End Node: new. Represents the sensor. Only reachable from/by the Edge
      Node (isolated last-hop segment, 192.168.100.0/24) -- not routed any
      further, so the Router does NOT get a third interface and the Edge
      Node does NOT need IP forwarding between its two NICs. Doesn't run
      any crypto -- see the ROLE placeholder comment below.

WHAT DIDN'T CHANGE (deliberately -- "mantém a segurança atual"): still the
same iotsim/pqc-static image, same fixed KEM/signature algorithm baked in
at build time, same ROLE=client/server env-var switch, same handshake
mechanism (host-side `docker exec` + openssl s_client, see
run_full_experiment_tese.py / run_minimal_experiment.py). None of that is
touched here -- this script only rebuilds the NETWORK the existing PQC
logic runs on top of.

MODEL PLACEHOLDER: the future adaptive decision model (README.md,
"Próximos passos") will run on the Edge Node once implemented. Anything
that's only needed for THAT and has no effect on network connectivity --
this step's actual goal -- is written out but commented, not wired up; see
the "model placeholder" comments below (env vars, mainly). Search for
"TODO (model placeholder)" to find all of them.

TWO GNS3 API FIXES this script depends on, made in gns3utils.py (see that
diff for the full reasoning):
  - create_docker_template() now takes an `adapters` kwarg (used to
    register the Edge Node's 2-NIC template below; default is still 1, so
    every other caller is unaffected).
  - set_node_network_interfaces() OVERWRITES /etc/network/interfaces on
    each call -- fine for single-NIC nodes, but calling it twice for the
    Edge Node's two NICs would silently lose the first one. Added
    set_node_multi_iface_network_interfaces() for exactly that case.

Prerequisites (same as before):
    make pqc_static                              # builds iotsim/pqc-static (in the static suite's folder)
    python3 create_templates_tese.py             # registers 'iotsim-pqc-static' (1 NIC), used by End Node + Server

This script additionally self-registers a second template,
'iotsim-pqc-static-edge' (2 NICs, same image), used only by the Edge Node
-- no separate Dockerfile/image needed, see module docstring above.

Run from the `src/tese` directory, with the GNS3 server running:
    (venv) $ python3 create_topology_edge_tese.py
"""

import ipaddress
import json
import sys
import time

from pathlib import Path

# gns3utils.py lives in <repo_root>/src, not on sys.path by default from
# this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gns3utils import *

# New project name (not "tese_pqc_adaptive") so the old switch-based
# project is left alone -- delete it yourself once this one is validated.
PROJECT_NAME = "tese_pqc_edge"
AUTO_CONFIGURE_ROUTER = True

ROUTER_TEMPLATE_NAME = "VyOS 1.3.0"
# 1-NIC template, shared with the static suite -- used for End Node and
# Server, both single-homed. Must already be registered.
ENDPOINT_TEMPLATE_NAME = "iotsim-pqc-static"
# 2-NIC template, same image, registered by THIS script if missing (see
# below) -- used only for the Edge Node, which needs a NIC facing the End
# Node and a NIC facing the Router.
EDGE_TEMPLATE_NAME = "iotsim-pqc-static-edge"
DOCKER_IMAGE = "iotsim/pqc-static:latest"

# The "clean" network profile doubles as the router's initial baseline
# config -- see network_profiles.py and ../../router/network_profiles/clean.sh.
# UNCHANGED from the switch-based topology: the router's own two zone
# addresses (101.0/24, 102.0/24) don't move, so clean.sh needs no edits.
ROUTER_CONFIG_SCRIPT = "../../router/network_profiles/clean.sh"
STATE_FILE = Path("topology_state.json")

# Sensor zone: End Node <-> Edge Node. New, isolated -- not routed past the
# Edge Node (see module docstring, "só pelo Edge Node" design choice), so
# there's no "gateway" for the Edge Node's side of it.
SENSOR_ZONE_CIDR = "192.168.100.0/24"
END_NODE_IP = ipaddress.IPv4Interface("192.168.100.10/24")
EDGE_NODE_SENSOR_IP = ipaddress.IPv4Interface("192.168.100.1/24")

# Edge zone: Edge Node <-> Router. Same addressing the old "client zone"
# used for pqc-client -- the Edge Node inherits that role and that address.
EDGE_ZONE_GATEWAY = "192.168.101.1"
EDGE_NODE_WAN_IP = ipaddress.IPv4Interface("192.168.101.10/24")

# Server zone: Router <-> Server. Completely unchanged.
SERVER_ZONE_GATEWAY = "192.168.102.1"
SERVER_IP = ipaddress.IPv4Interface("192.168.102.10/24")
SERVER_PORT = 4433

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())

check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)

if project:
    print(f"Project {PROJECT_NAME} exists. ", project)
else:
    project = create_project(server, PROJECT_NAME, 800, 1200)
    print("Created project ", project)

open_project_if_closed(server, project)

if len(get_all_nodes(server, project)) > 0:
    print("Project is not empty!")
    sys.exit(1)

templates = get_all_templates(server)

router_template_id = get_template_id_from_name(templates, ROUTER_TEMPLATE_NAME)
assert router_template_id, f"Router template '{ROUTER_TEMPLATE_NAME}' not found. Import the VyOS appliance first."

endpoint_template_id = get_template_id_from_name(templates, ENDPOINT_TEMPLATE_NAME)
assert endpoint_template_id, (
    f"Endpoint template '{ENDPOINT_TEMPLATE_NAME}' not found. "
    "Run create_templates_tese.py from the STATIC suite first (this suite reuses its image/template)."
)

edge_template_id = get_template_id_from_name(templates, EDGE_TEMPLATE_NAME)
if not edge_template_id:
    print(f"Template '{EDGE_TEMPLATE_NAME}' not found, registering it now "
          f"(same image as '{ENDPOINT_TEMPLATE_NAME}', but with 2 network adapters).")
    created = create_docker_template(server, EDGE_TEMPLATE_NAME, DOCKER_IMAGE, adapters=2)
    edge_template_id = created["template_id"]
    print(f"Registered '{EDGE_TEMPLATE_NAME}' (id={edge_template_id})")

input("Open the GNS3 project GUI. Press enter to continue...")

############
# TOPOLOGY #
############
#   End Node  --eth0..eth0--  Edge Node  --eth1..eth0--  Router (VyOS)  --eth1..eth0--  Server
#  (sensor)                  (2 NICs: sensor-facing        eth0 faces Edge,             (data)
#                             + router-facing)              eth1 faces Server

coord_router = Position(0, 0)
coord_edge = Position(coord_router.x - project.grid_unit * 6, coord_router.y)
coord_end = Position(coord_edge.x - project.grid_unit * 6, coord_router.y)
coord_server = Position(coord_router.x + project.grid_unit * 6, coord_router.y)

router = create_node(server, project, coord_router.x, coord_router.y, router_template_id, node_name="pqc-router")

if AUTO_CONFIGURE_ROUTER:
    print(f"Installing {router['name']}")
    hostname, port = get_node_telnet_host_port(server, project, router["node_id"])
    terminal_cmd = f"konsole -e telnet {hostname} {port}"
    start_node(server, project, router["node_id"])
    install_vyos_image_on_node(router["node_id"], hostname, port, pre_exec=terminal_cmd)
    time.sleep(10)
    print(f"Configuring {router['name']} with {ROUTER_CONFIG_SCRIPT}")
    start_node(server, project, router["node_id"])
    configure_vyos_image_on_node(router["node_id"], hostname, port, ROUTER_CONFIG_SCRIPT, pre_exec=terminal_cmd)
    time.sleep(10)

end_node = create_node(server, project, coord_end.x, coord_end.y, endpoint_template_id, node_name="end-node")
edge_node = create_node(server, project, coord_edge.x, coord_edge.y, edge_template_id, node_name="edge-node")
server_node = create_node(server, project, coord_server.x, coord_server.y, endpoint_template_id, node_name="pqc-server")

# End Node adapter 0 (its only NIC) <-> Edge Node adapter 0 (eth0, sensor-facing)
create_link(server, project, end_node["node_id"], 0, edge_node["node_id"], 0)
# Edge Node adapter 1 (eth1, router-facing) <-> Router adapter 0 (eth0, was the "client zone" port)
create_link(server, project, edge_node["node_id"], 1, router["node_id"], 0)
# Router adapter 1 (eth1, unchanged "server zone" port) <-> Server adapter 0 (its only NIC)
create_link(server, project, router["node_id"], 1, server_node["node_id"], 0)

# NOTE: no check_ipaddrs() here -- it reads each node's /etc/network/interfaces
# off disk, which doesn't exist yet at this point (the set_node_*_network_interfaces()
# calls below are what write it). It runs in boot_scenario_edge.py instead, after
# a create_topology_edge_tese.py run has already written every node's addresses.

# End Node: single NIC, sensor zone. Gateway points at the Edge Node's
# sensor-facing address even though nothing forwards through it yet (see
# module docstring, "só pelo Edge Node") -- harmless (on-link traffic to
# the Edge Node doesn't need the default route at all), and means this
# doesn't need editing again if the reachability scope is widened later.
set_node_network_interfaces(server, project, end_node["node_id"], "eth0", END_NODE_IP, str(EDGE_NODE_SENSOR_IP.ip))

# Edge Node: TWO NICs, written in a single call (see gns3utils.py fix in
# this script's module docstring -- calling set_node_network_interfaces()
# twice here would silently drop the first interface).
#   eth0: sensor zone, no gateway (isolated -- nothing beyond the End Node
#         is reachable from this NIC, by design).
#   eth1: edge zone, gateway = Router. Same address the old pqc-client had.
set_node_multi_iface_network_interfaces(server, project, edge_node["node_id"], [
    {"iface_name": "eth0", "ip_iface": EDGE_NODE_SENSOR_IP, "gateway": None},
    {"iface_name": "eth1", "ip_iface": EDGE_NODE_WAN_IP, "gateway": EDGE_ZONE_GATEWAY},
])

# Server: single NIC, server zone. Unchanged from the switch-based topology.
set_node_network_interfaces(server, project, server_node["node_id"], "eth0", SERVER_IP, SERVER_ZONE_GATEWAY)

# End Node env: ROLE=client is a PLACEHOLDER, not a statement that the End
# Node does PQC/TLS -- it doesn't, and won't until it has its own role.
# This image's entrypoint only special-cases ROLE=server (runs
# `openssl s_server`, needs to be listening for the suite to work);
# ROLE=client is the "idle, wait to be `docker exec`'d into" behaviour the
# static suite's pqc-client already relies on (see run_full_experiment_tese.py
# -- the client container never runs its own client process, the host
# controller execs `openssl s_client` into it on demand). Using it here
# just keeps the container from crashing/looping with an unrecognized ROLE
# until the End Node has real sensor-simulation logic.
end_node_env = {"ROLE": "client"}
# TODO (model placeholder -- not needed for this step, no effect on network
# connectivity): once the End Node actually generates sensor data and pushes
# it to the Edge Node, it will need to know where to send it, and the Edge
# Node will need something listening for it. Left commented out because
# nothing on the Edge Node listens for this yet.
# end_node_env["EDGE_NODE_HOST"] = str(EDGE_NODE_SENSOR_IP.ip)

# Edge Node env: identical to what the old "pqc-client" node had --
# "mantém a segurança atual". This node is now where the handshake is
# decided/performed (run_*_experiment.py will `docker exec` into it, same
# mechanism as before, just against a different node name/role).
edge_node_env = {"ROLE": "client", "SERVER_HOST": str(SERVER_IP.ip), "SERVER_PORT": str(SERVER_PORT)}
# TODO (model placeholder -- not needed for this step, no effect on network
# connectivity): this is the seam where the future adaptive decision model
# (README.md, "Próximos passos") would be configured/pointed at once
# implemented -- e.g. a path to a trained policy it should load, or an
# override for the fixed rotation_interval_s the experiment scripts pass in
# today. Left commented out -- there's no model to load yet, and the
# current scripts don't read this env var.
# edge_node_env["MODEL_CONFIG_PATH"] = "/models/rotation_policy.json"

server_env = {"ROLE": "server", "SERVER_PORT": str(SERVER_PORT)}

update_docker_node_environment(server, project, end_node["node_id"], environment_dict_to_string(end_node_env))
update_docker_node_environment(server, project, edge_node["node_id"], environment_dict_to_string(edge_node_env))
update_docker_node_environment(server, project, server_node["node_id"], environment_dict_to_string(server_env))

state = {
    "project_name": PROJECT_NAME,
    "project_id": project.id,
    "nodes": {
        "router": {"node_id": router["node_id"], "name": router["name"]},
        "end_node": {"node_id": end_node["node_id"], "name": end_node["name"]},
        "edge_node": {"node_id": edge_node["node_id"], "name": edge_node["name"]},
        "server": {"node_id": server_node["node_id"], "name": server_node["name"]},
    },
    "networks": {
        "sensor_zone": {"cidr": SENSOR_ZONE_CIDR, "end_node_ip": str(END_NODE_IP), "edge_node_ip": str(EDGE_NODE_SENSOR_IP)},
        "edge_zone": {"cidr": "192.168.101.0/24", "gateway": EDGE_ZONE_GATEWAY, "edge_node_ip": str(EDGE_NODE_WAN_IP)},
        "server_zone": {"cidr": "192.168.102.0/24", "gateway": SERVER_ZONE_GATEWAY, "server_ip": str(SERVER_IP)},
    },
    "server_port": SERVER_PORT,
    "endpoint_template": ENDPOINT_TEMPLATE_NAME,
    "edge_endpoint_template": EDGE_TEMPLATE_NAME,
    "current_network_profile": "clean",
}
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2)

print(f"\nTopology created. Role -> node_id mapping saved to {STATE_FILE.resolve()}")
print("No switches: End Node -- Edge Node -- Router -- Server are all direct point-to-point links.")
print("Edge Node performs the PQC handshake with the Server (same fixed algorithm as before).")
print("Next: boot_scenario_edge.py to bring the network up, then validate connectivity (ping) before")
print("touching run_minimal_experiment.py / run_full_experiment_tese.py -- those still assume the old")
print("node names (client/server) and will need updating for end_node/edge_node/server once the")
print("network itself is confirmed working.")