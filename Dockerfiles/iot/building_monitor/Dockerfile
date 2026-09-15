FROM ubuntu:jammy

RUN apt update && apt install -y --no-install-recommends \
    iputils-ping \
    sntp \
    python3 \
    python3-pip \
    wget \
    git \
    cmake \
    ninja-build \
    build-essential \
    libssl-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir paho-mqtt

ARG LIBOQS_TAG=0.13.0
ARG OQSPROVIDER_TAG=0.9.0

# --- liboqs (biblioteca dos algoritmos pós-quânticos) ---
RUN git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs.git /tmp/liboqs \
    && cmake -S /tmp/liboqs -B /tmp/liboqs/build -GNinja \
    && cmake --build /tmp/liboqs/build --parallel \
    && cmake --install /tmp/liboqs/build \
    && rm -rf /tmp/liboqs

# --- oqs-provider (plugin que liga o liboqs ao OpenSSL 3) ---
RUN git clone --depth 1 --branch ${OQSPROVIDER_TAG} https://github.com/open-quantum-safe/oqs-provider.git /tmp/oqs-provider \
    && cmake -S /tmp/oqs-provider -B /tmp/oqs-provider/build -GNinja \
    && cmake --build /tmp/oqs-provider/build --parallel \
    && cmake --install /tmp/oqs-provider/build \
    && rm -rf /tmp/oqs-provider

# --- dizer ao OpenSSL para carregar o provider ---
COPY openssl-oqs.cnf /etc/ssl/openssl-oqs.cnf
ENV OPENSSL_CONF=/etc/ssl/openssl-oqs.cnf

COPY appliances_energy/energydata_complete.csv.xz /energydata_complete.csv.xz
COPY --from=iotsim/certificates:latest /artifacts/pki/iot-sim-ca.crt /iot-sim-ca.crt

COPY client.py /client.py
RUN chmod +x /client.py

ENTRYPOINT ["/client.py"]