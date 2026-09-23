#!/usr/bin/env python3

import json
import lzma
import os
import random
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import joblib
import numpy as np
import paho.mqtt.publish as publish
import ipaddress

import shlex
from typing import List

import tls_groups


config = {"MQTT_BROKER_ADDR": "localhost",
          "MQTT_TOPIC_PUB": "building",
          "MQTT_AUTH": "",
          "MQTT_QOS": 0,
          "TLS": "",    # TLS GROUP from available TLS options: X25519MLKEM768:mlkem768:p384_mlkem768:x25519:secp256r1
          "TLS_INSECURE": "false",
          "MODEL_PATH": "./models/decision_model.joblib",          # trained decision model (joblib), must expose .predict()
          "NORMALIZATION_PATH": "./models/normalization.joblib",   # normalization fitted on the training data, see normalize_features()
          "SLEEP_TIME": 300,
          "SLEEP_TIME_SD": 1,
          "PING_SLEEP_TIME": 60,
          "PING_SLEEP_TIME_SD": 1,
          "ACTIVE_TIME": 60,
          "ACTIVE_TIME_SD": 0,
          "INACTIVE_TIME": 0,
          "INACTIVE_TIME_SD": 0,
          "NTP_SERVER": "localhost",
          "NTP_SLEEP_TIME": 60,
          "NTP_SLEEP_TIME_SD": 0,

          # --- CoAP ---
          # Example: "192.168.10.1-192.168.10.50;192.168.20.2". Empty = CoAP threads disabled.
          "COAP_ADDR_LIST": "192.168.17.10",
          "PSK": "",    # non-empty = use coaps with the key read from PSK_FILE
          "SLEEP_TIME_COAP": 300,
          "SLEEP_TIME_SD_COAP": 10,
          "PING_SLEEP_TIME_COAP": 600,
          "PING_SLEEP_TIME_SD_COAP": 10,
          "ACTIVE_TIME_COAP": 60,
          "ACTIVE_TIME_COAP_SD": 0,
          "INACTIVE_TIME_COAP": 0,
          "INACTIVE_TIME_COAP_SD": 0,
          }

PSK_FILE = "/opt/psk.txt"


def readloop(file, openfunc=open, skipfirst=True):
    """Read a file line by line. If EOF, start from beginning."""
    with openfunc(file, "r") as f:
        while True:
            if skipfirst:
                f.readline()
            for line in f:
                if isinstance(line, bytes):
                    yield line.decode(encoding="utf-8")
                else:
                    yield line
            f.seek(0, 0)


def as_json(payload):
    """Dictionary to json."""
    return json.dumps(payload)


# --- Decision model: per-reading adaptive TLS security level ---------------
#
# Each CSV row is classified by the pre-trained decision model into one of 3
# NIST post-quantum security levels. That level picks the TLS 1.3 group used
# for the MQTT publish of that particular reading, out of the groups already
# listed in config["TLS"]'s comment.
#
# change the keys below to match.

NIST_LEVEL_TO_TLS_GROUP = {
    0: "x25519",           # classical only     - lowest overhead / lowest security
    1: "mlkem768",          # pure PQC           - NIST category 3 equivalent
    2: "X25519MLKEM768",    # hybrid classical+PQC - highest security
}

# Used for the very first publish(es), before the decision model has had a
# chance to classify a reading (or if it's unavailable/errors out). Offered
# as a preference list (highest security first) rather than a single group,
# so the handshake still succeeds against a broker/oqs-provider build that
# doesn't support the top preference.
DEFAULT_TLS_GROUPS = "X25519MLKEM768:mlkem768:x25519:secp256r1"


def build_tls_context(ca_certs, tls_group, insecure):
    """Build a fresh client-side ssl.SSLContext with the TLS 1.3 key-exchange
    group(s) pinned to `tls_group` (single group or colon-separated
    preference list, e.g. "X25519MLKEM768" or DEFAULT_TLS_GROUPS).

    A *new* SSLContext is built per publish rather than reused, because the
    group has to change per reading -- reusing one context and calling
    set_groups() again between connections is unnecessary risk (no
    guarantee OpenSSL doesn't cache/derive anything from the previous
    negotiation), and a fresh SSLContext is cheap relative to the
    ~SLEEP_TIME publish cadence here.

    Raises ssl.SSLError if `tls_group` isn't recognized -- e.g. a PQC group
    name but the local OpenSSL build doesn't have oqs-provider loaded/
    configured (see tls_groups.py's module docstring and selftest()).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(ca_certs)
    if insecure:
        # Mirrors what paho's tls dict `insecure` flag does: keep the
        # channel encrypted but skip server hostname verification.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    tls_groups.set_groups(ctx, tls_group)
    return ctx


def normalize_features(features, normalizer):
    """Normalize a raw feature vector (shape (1, n_features)) with the fitted
    normalizer loaded from NORMALIZATION_PATH.

    Supports the two most common formats for that file:
      1. A fitted scikit-learn transformer (StandardScaler, MinMaxScaler...)
         saved with joblib -> exposes .transform().
      2. A plain dict (saved with joblib/pickle) with "mean"/"std" (z-score)
         or "min"/"max" (min-max) arrays, one value per feature.
    Adjust this function if your normalization file uses a different format.
    """
    if hasattr(normalizer, "transform"):
        return normalizer.transform(features)

    if isinstance(normalizer, dict):
        arr = np.asarray(features, dtype=float)
        if "mean" in normalizer and "std" in normalizer:
            mean = np.asarray(normalizer["mean"], dtype=float)
            std = np.asarray(normalizer["std"], dtype=float)
            std = np.where(std == 0, 1.0, std)  # avoid divide-by-zero on constant columns
            return (arr - mean) / std
        if "min" in normalizer and "max" in normalizer:
            lo = np.asarray(normalizer["min"], dtype=float)
            hi = np.asarray(normalizer["max"], dtype=float)
            span = np.where((hi - lo) == 0, 1.0, hi - lo)
            return (arr - lo) / span

    raise TypeError("Unrecognized normalization file format; adjust normalize_features() to match it.")


def classify_security_level(data_line, model, normalizer):
    """Classify one already-parsed CSV row and return the NIST security
    level (see NIST_LEVEL_TO_TLS_GROUP) predicted by the decision model.

    `data_line` must already have the same typing applied as in the
    telemetry loop (ints for columns 1:3, floats for columns 3:), i.e. every
    field except the leading date/time column is numeric. Change the slice
    below if the model expects a different subset/order of columns.
    """
    features = np.asarray(data_line[1:], dtype=float).reshape(1, -1)
    normalized = normalize_features(features, normalizer)
    prediction = model.predict(normalized)[0]
    return int(prediction)


def iprange(start_addr: str, end_addr: str = None) -> List[ipaddress.IPv4Address]:
    """Return a list of IPv4 addresses between two addresses, or itself if end_addr is None."""
    if not end_addr:
        return [ipaddress.IPv4Address(start_addr)]
    start = int(ipaddress.IPv4Address(start_addr))
    end = int(ipaddress.IPv4Address(end_addr))
    assert start <= end
    addresses = []
    for i in range(start, end+1):
        addresses.append(ipaddress.IPv4Address(i))
    return addresses


def signal_handler(signum, stackframe, event):
    """Set the event flag to signal all threads to terminate."""
    print(f"Handling signal {signum}")
    event.set()


def ping(bin_path, destination, attempts=3, wait=10):
    """Check if destination responds to ICMP echo requests."""
    for i in range(attempts):
        result = subprocess.run([bin_path, "-c1", destination], capture_output=False, check=False)
        if result.returncode == 0 or i==attempts-1:
            return result.returncode == 0
        time.sleep(wait)
    return result.returncode == 0


def coap_ping(sleep_t, sleep_t_sd, die_event, client_list, coap_bin):
    """Periodically send a coap '.well-known/core' request to a list of clients (coap servers)."""
    while not die_event.is_set():
        for client in client_list:
            resource = f"coap://{client}/.well-known/core"
            cmd = f"{coap_bin} -m GET {resource}"
            print(f"[  core   ] .well-known/core for {client}... ", end="")

            try:
                cmd_result = subprocess.run(shlex.split(cmd), capture_output=True, timeout=10, check=True)
                allok = True
            except subprocess.CalledProcessError:
                print("...ERROR!")
                allok = False
            except subprocess.TimeoutExpired:
                print("...TIMEOUT!")
                allok = False

            if allok:
                print("...OK.")

        sleep_time = random.gauss(sleep_t, sleep_t_sd)
        sleep_time = sleep_t if sleep_time < 0 else sleep_time
        print(f"[  core   ] sleeping for {sleep_time}s")
        die_event.wait(timeout=sleep_time)
    print("[  core   ] killing thread")


def broker_ping(sleep_t, sleep_t_sd, die_event, broker_addr, ping_bin):
    """Periodically send ICMP echo requests to the MQTT broker."""
    while not die_event.is_set():
        print(f"[  ping   ] pinging {broker_addr}...", end="")

        if ping(ping_bin, broker_addr, attempts=1, wait=1):
            print("...OK.")
        else:
            print("...ERROR!")

        sleep_time = random.gauss(sleep_t, sleep_t_sd)
        sleep_time = sleep_t if sleep_time < 0 else sleep_time
        print(f"[  ping   ] sleeping for {sleep_time}s")
        die_event.wait(timeout=sleep_time)
    print("[  ping   ] killing thread")


def ntp_client(sleep_t, sleep_t_sd, die_event, ntp_server, ntp_bin):
    """Periodically poll NTP server."""
    cmd = [ntp_bin, '--ipv4', ntp_server]
    while not die_event.is_set():
        print(f"[   ntp   ] polling NTP server {ntp_server}")

        result = subprocess.run(cmd, capture_output=False, check=False)
        if result.returncode > 0:
            print(f"[   ntp   ] {cmd[0]} failed with return code {result.returncode}")

        sleep_time = random.gauss(sleep_t, sleep_t_sd)
        sleep_time = sleep_t if sleep_time < 0 else sleep_time
        print(f"[   ntp   ] sleeping for {sleep_time}s")
        die_event.wait(timeout=sleep_time)
    print("[   ntp   ] killing thread")


def telemetry(sleep_t, sleep_t_sd, event, die_event, mqtt_topic, broker_addr, mqtt_auth, mqtt_qos, mqtt_tls, mqtt_cacert, mqtt_tls_insecure, decision_model, normalizer):
    """Periodically send sensor data to the MQTT broker.

    Before each publish, the reading just read from the CSV is classified by
    `decision_model` (see classify_security_level()); the resulting NIST
    level selects the TLS group used for that publish.
    """
    print("[telemetry] starting thread")
    dataset_fname = "/energydata_complete.csv.xz"
    dataset_fieldseparator = ","
    dataset_decimalseparator = "."
    dataset_columns = ["date",
                       "Appliances energy use",
                       "lights energy use",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Humidity",
                       "Temperature",
                       "Pressure",
                       "Humidity",
                       "Windspeed",
                       "Visibility",
                       "Tdewpoint",
                       "rv1",
                       "rv2"]
    dataset_zones = ["general",
                     "general",
                     "general",
                     "kitchen",
                     "kitchen",
                     "living-room",
                     "living-room",
                     "laundry-room",
                     "laundry-room",
                     "office-room",
                     "office-room",
                     "bathroom",
                     "bathroom",
                     "outside-north",
                     "outside-north",
                     "ironing-room",
                     "ironing-room",
                     "teenager-room-2",
                     "teenager-room-2",
                     "parents-room",
                     "parents-room",
                     "outside-station",
                     "general",
                     "outside-station",
                     "general",
                     "general",
                     "general",
                     "general",
                     "general"]
    dataset_units = ["year-month-day hour:minute:second",
                     "Wh",
                     "Wh",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "%",
                     "Celsius",
                     "mm Hg",
                     "%",
                     "m/s",
                     "km",
                     "°C",
                     "Random variable 1, (nondimensional)",
                     "Random variable 2, (nondimensional)"]

    try:
        with open(dataset_fname, "rb"):
            pass
    except Exception as e:
        print(f"[telemetry] error opening `{dataset_fname}'")
        print(e)
        die_event.set()
        print("[telemetry] killing thread")
        return

    data_iter = readloop(dataset_fname, lzma.open)
    print(f"[telemetry] opened `{dataset_fname}'")

    if mqtt_tls:
        current_tls_group = DEFAULT_TLS_GROUPS
        port = 8883
    else:
        current_tls_group = None
        port = 1883

    while not die_event.is_set():
        if event.is_set():
            data_line = next(data_iter).strip().split(dataset_fieldseparator)
            data_line[1:3] = list(map(int, data_line[1:3]))
            data_line[3:] = list(map(float, data_line[3:]))

            # --- adaptive TLS: classify this reading and pick the TLS group ---
            if mqtt_tls and decision_model is not None and normalizer is not None:
                try:
                    security_level = classify_security_level(data_line, decision_model, normalizer)
                    selected_group = NIST_LEVEL_TO_TLS_GROUP.get(security_level)
                    if selected_group is None:
                        print(f"[telemetry] decision model returned unexpected level {security_level}; keeping current TLS group '{current_tls_group}'")
                    else:
                        print(f"[telemetry] decision model -> NIST level {security_level} -> TLS group '{selected_group}'")
                        current_tls_group = selected_group
                except Exception as e:
                    print(f"[telemetry] decision model error: {e}; keeping current TLS group '{current_tls_group}'")

            # list of mqtt messages, each message = ("<topic>", "<payload>", qos, retain)
            msgs = []
            for zone in set(dataset_zones):
                relevant_idx = [i for i in range(len(dataset_zones)) if dataset_zones[i] == zone]
                payload = as_json({dataset_columns[i]: {"value":data_line[i], "units":dataset_units[i]} for i in relevant_idx})
                msgs.append((f"{mqtt_topic}/{zone}", payload, mqtt_qos, False))

            for msg in msgs:
                print(f"[telemetry] sending to `{broker_addr}' topic: `{msg[0]}'; qos `{msg[2]}'; payload: `{msg[1]}'")

            # publish multiple messages to the broker and disconnect cleanly.
            try:
                if mqtt_tls:
                    tls_kwarg = build_tls_context(mqtt_cacert, current_tls_group, mqtt_tls_insecure)
                else:
                    tls_kwarg = None
                publish.multiple(msgs, hostname=broker_addr, port=port, auth=mqtt_auth, tls=tls_kwarg)
            except ConnectionRefusedError as e:
                print(f"[telemetry] {e}")
                die_event.set()
            except ssl.SSLError as e:
                print(f"[telemetry] {e}")
                die_event.set()

            sleep_time = random.gauss(sleep_t, sleep_t_sd)
            sleep_time = sleep_t if sleep_time < 0 else sleep_time
            print(f"[telemetry] sleeping for {sleep_time}s")
            die_event.wait(timeout=sleep_time)
        else:
            print("[telemetry] zZzzZZz sleeping... zzZzZZz")
            event.wait(timeout=1)
    print("[telemetry] killing thread")


def telemetry_coap(sleep_t, sleep_t_sd, event, die_event, client_list, coap_bin, psk):
    """Periodically send a series of coap requests to a list of clients (coap servers)."""
    print("[requests ] starting thread")

    if psk:
        coap_scheme = "coaps"
        coap_identity = f"-k {psk} -u {socket.gethostname()}"
    else:
        coap_scheme = "coap"
        coap_identity = ""

    while not die_event.is_set():
        if event.is_set():
            for client in client_list:
                rcv_payload = {}
                resource_list = ["ambient_temperature", "exhaust_vacuum", "ambient_pressure",
                                 "relative_humidity", "energy_output"]
                for resource in resource_list:
                    uri = f"{coap_scheme}://{client}/{resource}"
                    cmd = f"{coap_bin} {coap_identity} -m GET {uri}"
                    print(f"[requests ] requesting resource `{uri}'...", end="")

                    try:
                        cmd_result = subprocess.run(shlex.split(cmd), capture_output=True, timeout=10, check=True)
                        allok = True
                    except subprocess.CalledProcessError:
                        print("...ERROR!")
                        allok = False
                        die_event.set()
                    except subprocess.TimeoutExpired:
                        print("...TIMEOUT!")
                        allok = False

                    if allok:
                        cmd_stdout = cmd_result.stdout.decode("utf-8").strip()
                        cmd_stderr = cmd_result.stderr.decode("utf-8").strip()
                        if cmd_stdout:
                            print("...OK.")
                            rcv_payload[resource] = cmd_stdout
                        else:
                            print(f"...{cmd_stderr}")

                    die_event.wait(timeout=max(0, random.gauss(0.5, 0.2)))

                print(f"[requests ] received payload from {client} = {rcv_payload}")

            sleep_time = random.gauss(sleep_t, sleep_t_sd)
            sleep_time = sleep_t if sleep_time < 0 else sleep_time
            print(f"[requests ] sleeping for {sleep_time}s")
            die_event.wait(timeout=sleep_time)
        else:
            print("[requests ] ZzZZzzZ sleeping... ZZzZzzZ")
            event.wait(timeout=1)
    print("[requests ] killing thread")


def activity_scheduler(tag, event, die_event, active_t, active_t_sd, inactive_t, inactive_t_sd):
    """Toggle `event` ON for ~active_t seconds and OFF for ~inactive_t seconds, until die_event is set.

    MQTT and CoAP each get their own event + scheduler, so their ON/OFF
    cycles (ACTIVE_TIME* / INACTIVE_TIME*) are independent.
    """
    while not die_event.is_set():
        event.set()
        print(f"[{tag}] telemetry ON")
        die_event.wait(timeout=max(0, random.gauss(active_t, active_t_sd)))
        if inactive_t > 0 and not die_event.is_set():
            event.clear()
            print(f"[{tag}] telemetry OFF")
            die_event.wait(timeout=max(0, random.gauss(inactive_t, inactive_t_sd)))


def main(conf):
    """Manages the other threads."""
    event = threading.Event()        # MQTT telemetry ON/OFF
    event_coap = threading.Event()   # CoAP telemetry ON/OFF
    die_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda a,b:signal_handler(a, b, die_event))

    telemetry_thread = threading.Thread(target=telemetry,
                                        name="telemetry",
                                        args=(conf["SLEEP_TIME"],
                                              conf["SLEEP_TIME_SD"],
                                              event, die_event,
                                              conf["MQTT_TOPIC_PUB"], conf["MQTT_BROKER_ADDR"], conf["mqtt_auth"], conf["MQTT_QOS"],
                                              conf["TLS"], conf["ca_cert_file"], conf["tls_insecure"],
                                              conf["decision_model"], conf["normalizer"]),
                                        kwargs={})
    broker_ping_thread = threading.Thread(target=broker_ping,
                                          name="broker_ping",
                                          args=(conf["PING_SLEEP_TIME"], conf["PING_SLEEP_TIME_SD"], die_event, conf["MQTT_BROKER_ADDR"], conf["ping_bin"]),
                                          kwargs={},
                                          daemon=False)
    ntp_client_thread = threading.Thread(target=ntp_client,
                                         name="ntp_client",
                                         args=(conf["NTP_SLEEP_TIME"], conf["NTP_SLEEP_TIME_SD"], die_event, conf["NTP_SERVER"], conf["ntp_bin"]),
                                         kwargs={},
                                         daemon=False)
    telemetry_coap_thread = threading.Thread(target=telemetry_coap,
                                                 name="telemetry",
                                                 args=(conf["SLEEP_TIME_COAP"], conf["SLEEP_TIME_SD_COAP"], event, die_event, conf["COAP_ADDR_LIST"], conf["coap_bin"], conf["PSK"]),
                                                 kwargs={})
    coap_ping_thread = threading.Thread(target=coap_ping,
                                   name="coap ping",
                                   args=(conf["PING_SLEEP_TIME_COAP"], conf["PING_SLEEP_TIME_SD_COAP"], die_event, conf["COAP_ADDR_LIST"], conf["coap_bin"]),
                                   kwargs={}, daemon=False)

    die_event.clear()
    broker_ping_thread.start()
    coap_ping_thread.start()
    telemetry_thread.start()
    telemetry_coap_thread.start()

    if conf["ntp_bin"]:
        ntp_client_thread.start()
    die_event.wait(timeout=5)

    print("[  main   ] starting loop")

    # MQTT ON/OFF cycle runs in the main thread
    activity_scheduler("  main   ] [mqtt", event, die_event,
                       conf["ACTIVE_TIME"], conf["ACTIVE_TIME_SD"],
                       conf["INACTIVE_TIME"], conf["INACTIVE_TIME_SD"])

    print("[  main   ] exit")


if __name__ == "__main__":
    for key in config.keys():
        try:
            config[key] = os.environ[key]
        except KeyError:
            pass

    config["MQTT_QOS"] = int(config["MQTT_QOS"])
    for c in ("SLEEP_TIME", "SLEEP_TIME_SD", "PING_SLEEP_TIME", "PING_SLEEP_TIME_SD", "ACTIVE_TIME", "ACTIVE_TIME_SD", "INACTIVE_TIME", "INACTIVE_TIME_SD", "NTP_SLEEP_TIME", "NTP_SLEEP_TIME_SD",
              "SLEEP_TIME_COAP", "SLEEP_TIME_SD_COAP", "PING_SLEEP_TIME_COAP", "PING_SLEEP_TIME_SD_COAP", "ACTIVE_TIME_COAP", "ACTIVE_TIME_COAP_SD", "INACTIVE_TIME_COAP", "INACTIVE_TIME_COAP_SD"):
        config[c] = float(config[c])

    config["MQTT_TOPIC_PUB"] = f"{config['MQTT_TOPIC_PUB']}/id-{socket.gethostname()}"
    print(f"[  setup  ] selected MQTT topic: {config['MQTT_TOPIC_PUB']}")

    if config["MQTT_AUTH"]:
        user_pass = config["MQTT_AUTH"].split(":", 1)
        if len(user_pass) == 1:
            config["mqtt_auth"] = {"username": user_pass[0], "password": None}
        else:
            config["mqtt_auth"] = {"username": user_pass[0], "password": user_pass[-1]}
    else:
        config["mqtt_auth"] = None
    print(f"[  setup  ] MQTT authentication: {config['mqtt_auth']}")

    config["ping_bin"] = shutil.which("ping")
    if not config["ping_bin"]:
        sys.exit("[  setup  ] No 'ping' binary found. Exiting.")

    config["ntp_bin"] = shutil.which("sntp")
    if not config["ntp_bin"]:
        print("[  setup  ] No 'sntp' binary found.")
    if config["NTP_SLEEP_TIME"] <= 0:
        config["ntp_bin"] = None
        print("[  setup  ] Disabling ntp.")

    # Load the decision model + normalization file once. They're then handed
    # to the telemetry thread, which calls classify_security_level() on every
    # CSV row to pick that reading's TLS group (see NIST_LEVEL_TO_TLS_GROUP).
    try:
        config["decision_model"] = joblib.load(config["MODEL_PATH"])
        config["normalizer"] = joblib.load(config["NORMALIZATION_PATH"])
        print(f"[  setup  ] loaded decision model from `{config['MODEL_PATH']}' "
              f"and normalization file from `{config['NORMALIZATION_PATH']}'")
    except FileNotFoundError as e:
        sys.exit(f"[  setup  ] could not load decision model / normalization file: {e}")

    if not ping(config["ping_bin"], config["MQTT_BROKER_ADDR"]):
        sys.exit(f"[  setup  ] {config['MQTT_BROKER_ADDR']} is down")

    if config["TLS"]:
        config["TLS"] = True
        config["ca_cert_file"] = "/iot-sim-ca.crt"
        # With tls_insecure=True communications are encrypted but the server hostname verification is disabled
        config["tls_insecure"] = config["TLS_INSECURE"].casefold() == "true"
        if not os.path.isfile(config["ca_cert_file"]):
            sys.exit(f"[  setup  ] TLS enabled but ca cert file `{config['ca_cert_file']}' does not exist. Exiting.")
    else:
        config["TLS"] = False
        config["ca_cert_file"] = None
        config["tls_insecure"] = None

    print(f"[  setup  ] TLS enabled: {config['TLS']}, ca cert: {config['ca_cert_file']}, TLS insecure: {config['tls_insecure']}")

    # --- CoAP setup ---------------------------------------------------------
    # Empty COAP_ADDR_LIST => CoAP threads disabled, MQTT keeps running.
    config["coap_enabled"] = bool(config["COAP_ADDR_LIST"].strip())
    config["coap_bin"] = None

    if not config["coap_enabled"]:
        print("[  setup  ] COAP_ADDR_LIST is empty. CoAP threads disabled.")
        config["COAP_ADDR_LIST"] = []
        config["PSK"] = None
    else:
        address_list = []
        # config["COAP_ADDR_LIST"] example: "192.168.10.1-192.168.10.50;192.168.20.2"
        for ip_range in list(map(str.strip, config["COAP_ADDR_LIST"].split(";"))):
            if ip_range:
                address_list.extend(iprange(*list(map(str.strip, ip_range.split("-")))))
        config["COAP_ADDR_LIST"] = address_list

        config["coap_bin"] = shutil.which("coap-client", path=os.environ.get("PATH", "") + ":/opt")
        if not config["coap_bin"]:
            sys.exit("[  setup  ] No 'coap-client' binary found. Exiting.")

        for ip_addr in config["COAP_ADDR_LIST"]:
            print(f"[  setup  ] pinging {ip_addr}")
            if not ping(config["ping_bin"], str(ip_addr), attempts=3, wait=10):
                sys.exit(f"[  setup  ] {ip_addr} is down")

        if config["PSK"]:
            print("[  setup  ] With pre-shared key")
            try:
                with open(PSK_FILE, "r") as f:
                    config["PSK"] = f.read().strip()
            except FileNotFoundError:
                print(f"[  setup  ] Error opening {PSK_FILE}")
                config["PSK"] = None
            print(f"[  setup  ] Pre-shared key is: `{config['PSK']}'")
        else:
            print("[  setup  ] NO pre-shared key")
            config["PSK"] = None

    main(config)