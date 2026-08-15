# Static PQC (Post-Quantum Cryptography) endpoint for the thesis experiment.
#
# The KEM group (key exchange) and signature algorithm (certificates) are
# BOTH FIXED at build time via PQC_KEM_GROUP / PQC_SIG_ALG. There is no
# negotiation logic and nothing is switched at runtime: this is the
# *static* baseline the dissertation compares against the adaptive
# protocol-selection model (a separate step, implemented on top of this
# same network -- see ../../tese/README.md).
#
# Based on the official Open Quantum Safe build of OpenSSL3 + oqs-provider
# (liboqs + OpenSSL/master + oqs-provider), which already ships a working
# `openssl` CLI with the provider enabled:
#   https://github.com/open-quantum-safe/oqs-provider
#   https://hub.docker.com/r/openquantumsafe/oqs-ossl3
FROM openquantumsafe/oqs-ossl3:latest

# NIST FIPS 203/204 standardized names, as exposed by oqs-provider >= 0.9
# with OpenSSL >= 3.5 (this image tracks liboqs/oqs-provider master, which
# is well past that):
#   mlkem768 -> ML-KEM-768 (key exchange, NIST security category 3)
#   mldsa65  -> ML-DSA-65  (signature,    NIST security category 3)
# Both are chosen PURE post-quantum (no classical curve mixed in), because
# the comparison this thesis needs is PQC static vs. PQC adaptive, not
# PQC vs. classical. Override at build time for a different static
# baseline, e.g.:
#   docker build --build-arg PQC_KEM_GROUP=x25519_mlkem768 -t iotsim/pqc-static .
# for a hybrid classical+PQC baseline instead.
ARG PQC_KEM_GROUP=mlkem768
ARG PQC_SIG_ALG=mldsa65

ENV PQC_KEM_GROUP=${PQC_KEM_GROUP} \
    PQC_SIG_ALG=${PQC_SIG_ALG} \
    SERVER_PORT=4433 \
    ROLE=server

# Static (build-time) certificate material: a small lab CA and one server
# certificate/key, both signed with the fixed PQC_SIG_ALG. Generated once
# here on purpose -- certificate/key generation cost must not leak into
# the per-handshake benchmark measured later at runtime, and "static"
# should apply to the whole suite, not just the KEM.
RUN mkdir -p /certs && \
    openssl req -x509 -new -newkey "${PQC_SIG_ALG}" \
        -keyout /certs/ca.key -out /certs/ca.crt \
        -nodes -subj "/CN=tese-pqc-ca" -days 3650 && \
    openssl req -new -newkey "${PQC_SIG_ALG}" \
        -keyout /certs/server.key -out /certs/server.csr \
        -nodes -subj "/CN=pqc-server" && \
    openssl x509 -req -in /certs/server.csr -out /certs/server.crt \
        -CA /certs/ca.crt -CAkey /certs/ca.key -CAcreateserial -days 3650 && \
    rm -f /certs/server.csr

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
