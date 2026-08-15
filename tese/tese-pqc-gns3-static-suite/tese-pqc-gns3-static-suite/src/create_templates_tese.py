"""Register the GNS3 docker template for the thesis' static-PQC endpoint image.

This is intentionally separate from create_templates.py (which only knows
about the Makefile's `all` target and would not see `pqc_static`, since
that target is deliberately excluded from `all` -- see ../tese/README.md).

Run once, after `make pqc_static` has built the `iotsim/pqc-static` image,
and before create_topology_tese.py. GNS3 server must be running.

    (venv) $ python3 create_templates_tese.py
"""

from gns3utils import Server, check_server_version, create_docker_template, get_all_templates, get_template_id_from_name, read_local_gns3_config

TEMPLATE_NAME = "iotsim-pqc-static"
DOCKER_IMAGE = "iotsim/pqc-static:latest"

server = Server(*read_local_gns3_config())
check_server_version(server)

templates = get_all_templates(server)
if get_template_id_from_name(templates, TEMPLATE_NAME):
    print(f"Template '{TEMPLATE_NAME}' already exists, nothing to do.")
else:
    created = create_docker_template(server, TEMPLATE_NAME, DOCKER_IMAGE)
    print(f"Created template '{TEMPLATE_NAME}' (id={created['template_id']}) for image {DOCKER_IMAGE}")
