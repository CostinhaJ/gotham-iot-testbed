import re
import sys
import time

import docker

from gns3utils import *

PROJECT_NAME = "gotham_scenario"

check_resources()
check_local_gns3_config()
server = Server(*read_local_gns3_config())

check_server_version(server)

project = get_project_by_name(server, PROJECT_NAME)

if project:
    print(f"Project {PROJECT_NAME} exists. ", project)
else:
    print(f"Project {PROJECT_NAME} does not exsist!")
    sys.exit(1)

open_project_if_closed(server, project)

if len(get_all_nodes(server, project)) == 0:
    print(f"Project {PROJECT_NAME} is empty!")
    sys.exit(1)

check_ipaddrs(server, project)

docker_client = docker.from_env()
docker_client.ping()


start_capture_all_iot_links(server, project, re.compile("openvswitch.*", re.IGNORECASE), re.compile("iotsim-ip-camera-museum-*", re.IGNORECASE))


# stop_capture_all_iot_links(server, project, re.compile("openvswitch.*", re.IGNORECASE), re.compile("iotsim-ip-camera-museum-*", re.IGNORECASE))