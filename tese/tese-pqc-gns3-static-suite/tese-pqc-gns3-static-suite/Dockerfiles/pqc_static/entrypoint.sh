#!/bin/sh
# Static PQC endpoint entrypoint. POSIX /bin/sh on purpose (no bashisms):
# the base image's shell is not guaranteed to be bash.
#
# ROLE=server (default): runs openssl s_server bound to SERVER_PORT,
# restricted to the single fixed PQC_KEM_GROUP -- no other group is ever
# offered, so the suite in use cannot drift between runs.
#
# ROLE=client: does NOT benchmark by itself. It just stays up so that the
# orchestration script on the GNS3/Docker host (run_benchmark_tese.py)
# can `docker exec` individual, precisely-timed `openssl s_client`
# handshakes against the server, one at a time. Timing is done on the
# host side with Python's time.perf_counter() rather than in this shell,
# because portable sub-second timing across arbitrary /bin/sh
# implementations (dash/ash/busybox all differ) is not reliable enough
# for a thesis measurement -- see tese/README.md.
set -eu

case "${ROLE}" in
    server)
        echo "[pqc-static] TLS 1.3 server on :${SERVER_PORT}, group=${PQC_KEM_GROUP}, sig=${PQC_SIG_ALG}"
        exec openssl s_server \
            -accept "${SERVER_PORT}" \
            -cert /certs/server.crt -key /certs/server.key \
            -CAfile /certs/ca.crt \
            -groups "${PQC_KEM_GROUP}" \
            -tls1_3 \
            -quiet
        ;;
    client)
        echo "[pqc-static] client node ready (group=${PQC_KEM_GROUP}, sig=${PQC_SIG_ALG})."
        echo "[pqc-static] waiting for benchmark commands via 'docker exec' (run_benchmark_tese.py)."
        exec tail -f /dev/null
        ;;
    *)
        echo "Unknown ROLE '${ROLE}'. Set ROLE=server or ROLE=client." >&2
        exit 1
        ;;
esac
