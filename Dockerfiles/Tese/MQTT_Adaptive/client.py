#!/usr/bin/env python3

import math
import os
import random
import ssl
import time
from collections import Counter
from typing import Tuple

import numpy as np
import paho.mqtt.client as mqtt
from joblib import load

# --- Config (mesma convenção do client.py original do projeto) ---
config = {"MQTT_BROKER_ADDR": "localhost",
          "MQTT_TOPIC_PUB": "test/topic",
          "SLEEP_TIME": 1,
          "SLEEP_TIME_SD": 0.1}

for key in config.keys():
    try:
        config[key] = os.environ[key]
    except KeyError:
        pass

for c in ("SLEEP_TIME", "SLEEP_TIME_SD"):
    config[c] = float(config[c])

config["MQTT_TOPIC_PUB"] = config["MQTT_TOPIC_PUB"] + "/" + os.environ["HOSTNAME"]

CA_CERT_PATH = "/iot-sim-ca.crt"

# --- Modelos e pré-processadores gerados pelo script de treino ---
# (precisa da correção que guarda scaler/encoders -- ver nota em separado)
classifier = load("/context_classifier.joblib")
regressor = load("/context_regressor.joblib")
scaler = load("/context_scaler.joblib")
e_alg_encoder = load("/context_e_alg_encoder.joblib")
data_type_encoder = load("/context_data_type_encoder.joblib")

# Constantes de normalização copiadas do script de treino. Têm de bater certo
# com a escala do dataset original (hybrid.csv) -- vale a pena confirmares
# que os valores em runtime (tamanho de pacote, entropia) caem na mesma gama
# que o modelo viu no treino, ou as previsões vão ficar sempre perto do mesmo valor.
PCK_SIZE_MAX = 765.0
ENTROPY_MAX = 5.59

_peer_ips_seen = set()


def shannon_entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    length = len(data)
    return -sum((n / length) * math.log2(n / length) for n in counts.values())


def compute_context_features(message: bytes, broker_addr: str) -> np.ndarray:
    """Replica generate_context_features() do script de treino, mas a partir
    de sinais reais do próprio dispositivo em vez de colunas extraídas de pcap."""
    pck_size = len(message)
    entropy = shannon_entropy(message)
    _peer_ips_seen.add(broker_addr)

    # TODO: sem acesso direto à janela TCP real a este nível; valor fixo por agora.
    battery = 100.0
    cpu_usage = min(90.0, max(10.0, (pck_size / PCK_SIZE_MAX) * 50.0 + random.gauss(0, 5)))
    memory_usage = min(90.0, max(10.0, (entropy / ENTROPY_MAX) * 60.0 + random.gauss(0, 5)))
    data_size = pck_size / PCK_SIZE_MAX
    sensitivity = min(1.0, max(0.0, (entropy / ENTROPY_MAX) * 0.6 + (pck_size / PCK_SIZE_MAX) * 0.4))

    # Este dispositivo só fala MQTT sobre TCP -- nunca vai ter HTTP nem UDP=1,
    # por isso data_type sai sempre "other". É esperado, não é um erro; só significa
    # que esta feature não vai contribuir variância para ESTE tipo de dispositivo.
    data_type = data_type_encoder.transform(["other"])[0]

    bandwidth = min(100.0, len(_peer_ips_seen) * 20.0)
    # TODO: sem a definição original de "classe de porta" (Portcl_src/Portcl_dst)
    # do pipeline de extração dos pcaps, latency/congestion ficam a 0 por agora.
    latency = 0.0
    congestion = 0.0

    return np.array([[battery, cpu_usage, memory_usage, data_size, sensitivity,
                       data_type, bandwidth, latency, congestion]])


def decide(message: bytes) -> Tuple[str, float]:
    features = compute_context_features(message, config["MQTT_BROKER_ADDR"])
    features_scaled = scaler.transform(features)
    e_alg_pred = classifier.predict(features_scaled)[0]
    k_freq_pred = regressor.predict(features_scaled)[0]
    e_alg = e_alg_encoder.inverse_transform([e_alg_pred])[0]
    return e_alg, float(k_freq_pred)


def tau_minutes(kfreq_rph: float) -> float:
    kappa_min_rph = 0.1
    tau_max_min = 600.0
    return min(60.0 / max(kfreq_rph, kappa_min_rph), tau_max_min)


def tier_for(e_alg: str) -> str:
    """ASCON-128 = sem TLS (porta 1883). Os outros dois = TLS 1.3 com a
    cipher suite correspondente (porta 8883)."""
    if e_alg == "ASCON-128":
        return "plain"
    return e_alg  # "AES-128-GCM" ou "ChaCha20-Poly1305"


CIPHERSUITE_MAP = {
    "AES-128-GCM": "TLS_AES_128_GCM_SHA256",
    "ChaCha20-Poly1305": "TLS_CHACHA20_POLY1305_SHA256",
}


def on_connect(client, userdata, flags, rc):
    print(mqtt.connack_string(rc))


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print("Unexpected disconnection.")


def build_client(tier: str) -> mqtt.Client:
    c = mqtt.Client()
    c.on_connect = on_connect
    c.on_disconnect = on_disconnect
    if tier == "plain":
        c.connect(host=config["MQTT_BROKER_ADDR"], port=1883, keepalive=30)
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(CA_CERT_PATH)
        context.set_ciphersuites(CIPHERSUITE_MAP[tier])
        # Se o handshake falhar por verificação de hostname (o certificado do
        # broker pode não ter o nome/IP do container nos SANs), experimenta:
        #   context.check_hostname = False
        c.tls_set_context(context)
        c.connect(host=config["MQTT_BROKER_ADDR"], port=8883, keepalive=30)
    c.loop_start()
    return c


# --- Decisão inicial ---
first_message = f"{random.gauss(10, 1):.2f}".encode()
e_alg, k_freq = decide(first_message)
current_tier = tier_for(e_alg)
client = build_client(current_tier)
next_rotation = time.monotonic() + tau_minutes(k_freq) * 60
print(f"Início: {e_alg} (k_freq={k_freq:.2f} rot/h) -> tier={current_tier}")

while True:
    message = f"{random.gauss(10, 1):.2f}"
    sleep_time = random.gauss(config["SLEEP_TIME"], config["SLEEP_TIME_SD"])
    sleep_time = config["SLEEP_TIME"] if sleep_time < 0 else sleep_time
    time.sleep(sleep_time)

    e_alg, k_freq = decide(message.encode())
    new_tier = tier_for(e_alg)
    force_rotation = time.monotonic() >= next_rotation

    if new_tier != current_tier or force_rotation:
        reason = "mudança de algoritmo" if new_tier != current_tier else "rotação de chave"
        print(f"A reconectar ({reason}): {current_tier} -> {new_tier}")
        client.loop_stop()
        client.disconnect()
        client = build_client(new_tier)
        current_tier = new_tier
        next_rotation = time.monotonic() + tau_minutes(k_freq) * 60

    client.publish(topic=config["MQTT_TOPIC_PUB"], payload=message)