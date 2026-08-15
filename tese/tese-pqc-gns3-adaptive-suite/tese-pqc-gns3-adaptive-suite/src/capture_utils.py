"""Per-trial packet capture: start/stop, download, and parse.

run_scenario_tese.py (static suite) starts one GNS3/Wireshark capture for
the whole scenario and never stops or parses it -- fine for eyeballing
in Wireshark by hand, not for an automated dataset. Here, capture is
scoped to exactly one trial (one rotation-interval x network profile x
device profile combination) at a time: started fresh, stopped,
downloaded, and parsed before the next trial begins, so trials never
blend into one unbounded pcap (see run_experiment_tese.py).

TLS 1.3 decryption note: everything after ServerHello (EncryptedExtensions,
Certificate, CertificateVerify, Finished) is encrypted under handshake
traffic secrets. Passive capture alone cannot see the Certificate
message -- exactly the field this dataset most needs, since PQC
certificates dominate handshake bytes. The algorithm is fixed for the
whole run (see run_experiment_tese.py), so per-handshake Certificate
size won't vary across trials -- what varies is how many times per
trial it gets paid, which is the point: `trial_pcap_total_bytes` scaled
by `trial_rotation_count` is the bandwidth cost of a given rotation
frequency. To decrypt it, the benchmark handshake command is run with
OpenSSL's `-keylogfile <path>` (see run_experiment_tese.py), and the
resulting NSS key log (openssl appends to it, so all rotations in a
trial share one file) is downloaded from the client container and
handed to tshark here via the `tls.keylog_file` preference. Without it,
`certificate_bytes` will be 0 -- not silently wrong, just uninformative.
"""

import re
import sys

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pyshark

# See create_templates_adaptive_tese.py's comment: gns3utils.py lives in
# <repo_root>/src, not on sys.path by default from this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from gns3utils import download_capture_file, get_links_id_from_node_connected_to_name_regexp, start_capture, stop_capture


def get_capture_links(server, project, state: Dict) -> Tuple[List[str], List[str]]:
    """Resolve the (client-side, server-side) link IDs once per run.

    Mirrors the link lookup in run_scenario_tese.py's START_CAPTURE block.
    """
    nodes = state["nodes"]
    client_links = get_links_id_from_node_connected_to_name_regexp(
        server, project, nodes["switch_client"]["node_id"], re.compile(re.escape(nodes["client"]["name"])))
    server_links = get_links_id_from_node_connected_to_name_regexp(
        server, project, nodes["switch_server"]["node_id"], re.compile(re.escape(nodes["server"]["name"])))
    return [lk.id for lk in client_links], [lk.id for lk in server_links]


def start_trial_capture(server, project, client_link_ids: List[str], server_link_ids: List[str]) -> None:
    start_capture(server, project, client_link_ids)
    start_capture(server, project, server_link_ids)


def stop_trial_capture_and_download(server, project, client_link_ids: List[str], server_link_ids: List[str],
                                     dest_dir: Path, trial_id: str) -> Path:
    """Stop capture on both links, download only the client-side pcap.

    The client-side (IoT-device-facing) link is the one this dataset's
    packet metrics are computed from -- see module docstring on scope.
    The server-side capture is still stopped (freeing GNS3's capture
    resources) and downloaded for completeness/manual inspection, but is
    not parsed by parse_trial_pcap() below.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    client_results = stop_capture(server, project, client_link_ids)
    server_results = stop_capture(server, project, server_link_ids)

    client_pcap = dest_dir / f"{trial_id}_client.pcap"
    download_capture_file(server, project, client_results[0]["capture_file_name"], str(client_pcap))

    if server_results:
        server_pcap = dest_dir / f"{trial_id}_server.pcap"
        download_capture_file(server, project, server_results[0]["capture_file_name"], str(server_pcap))

    return client_pcap


def fetch_and_reset_keylog(client_container, container_keylog_path: str = "/tmp/keylog.txt") -> Optional[bytes]:
    """Read the accumulated OpenSSL keylog from the client container, then
    delete it so the next trial starts from an empty file.

    openssl s_client opens -keylogfile in append mode, so every handshake
    in a trial (see run_experiment_tese.py) contributes one entry to the
    same file -- no merging needed, just read once at trial end.
    """
    exit_code, out = client_container.exec_run(["cat", container_keylog_path])
    client_container.exec_run(["rm", "-f", container_keylog_path])
    if exit_code != 0 or not out:
        return None
    return out


def parse_trial_pcap(pcap_path: Path, keylog_bytes: Optional[bytes], keylog_dir: Path, trial_id: str) -> Dict:
    """Extract per-trial aggregate metrics from a downloaded pcap.

    Returns a dict matching the trial_* pcap columns in the dataset
    schema (see ../tese/README.md): total bytes, ClientHello/ServerHello
    /Certificate message sizes, TCP retransmission count, and capture
    duration.
    """
    override_prefs = {}
    if keylog_bytes:
        keylog_path = keylog_dir / f"{trial_id}_keylog.txt"
        keylog_path.write_bytes(keylog_bytes)
        override_prefs["tls.keylog_file"] = str(keylog_path)

    cap = pyshark.FileCapture(str(pcap_path), override_prefs=override_prefs, keep_packets=False)

    total_bytes = 0
    clienthello_bytes = 0
    serverhello_bytes = 0
    certificate_bytes = 0
    tcp_retransmits = 0
    timestamps = []

    for pkt in cap:
        total_bytes += int(pkt.length)
        timestamps.append(float(pkt.sniff_timestamp))

        if "tcp" in pkt and hasattr(pkt.tcp, "analysis_retransmission"):
            tcp_retransmits += 1

        if "tls" in pkt:
            # Assumes one handshake message per frame, true for this lab's
            # small, uncongested traffic -- a frame coalescing multiple
            # handshake messages would undercount. Good enough for a
            # controlled testbed; would need revisiting for WAN-scale traffic.
            handshake_type = getattr(pkt.tls, "handshake_type", None)
            if handshake_type == "1":
                clienthello_bytes += int(pkt.length)
            elif handshake_type == "2":
                serverhello_bytes += int(pkt.length)
            elif handshake_type == "11":
                certificate_bytes += int(pkt.length)

    cap.close()

    duration = (max(timestamps) - min(timestamps)) if timestamps else 0.0
    return {
        "trial_pcap_total_bytes": total_bytes,
        "trial_clienthello_bytes": clienthello_bytes,
        "trial_serverhello_bytes": serverhello_bytes,
        "trial_certificate_bytes": certificate_bytes,
        "trial_tcp_retransmits": tcp_retransmits,
        "trial_capture_duration_s": round(duration, 3),
    }
