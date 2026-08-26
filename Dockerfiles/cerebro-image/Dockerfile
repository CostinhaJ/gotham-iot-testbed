# ============================================================
# Componente CÉREBRO: Prometheus + Grafana num único container
# Nó Docker nativo para o GNS3
# ============================================================

FROM prom/prometheus:latest AS prometheus
FROM grafana/grafana:latest AS grafana

FROM ubuntu:22.04

RUN apt-get update && apt-get install -y \
        supervisor \
        ca-certificates \
        curl \
        iproute2 \
        iputils-ping \
        default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# ---- Telegraf (SNMP -> Prometheus) ----
# Verifica se esta versão ainda existe em https://dl.influxdata.com/telegraf/releases/
# antes do build; atualiza o número de versão se necessário.
RUN curl -sL https://dl.influxdata.com/telegraf/releases/telegraf-1.31.3_linux_amd64.tar.gz -o /tmp/telegraf.tar.gz \
    && tar xzf /tmp/telegraf.tar.gz -C /tmp \
    && cp /tmp/telegraf-1.31.3/usr/bin/telegraf /usr/local/bin/telegraf \
    && rm -rf /tmp/telegraf*
COPY telegraf.conf /etc/telegraf/telegraf.conf

# ---- sFlow-RT (coletor de amostras sFlow do OVS) ----
# Verifica se este link ainda é válido em https://sflow-rt.com/download.php
# antes do build (pode mudar); confirma também o script de arranque
# depois de extrair (deve ser /opt/sflow-rt/start.sh).
RUN curl -sL https://inmon.com/products/sFlow-RT/sflow-rt.tar.gz -o /tmp/sflow-rt.tar.gz \
    && tar xzf /tmp/sflow-rt.tar.gz -C /opt \
    && rm /tmp/sflow-rt.tar.gz

# ---- Binários e ficheiros base do Prometheus ----
COPY --from=prometheus /bin/prometheus /usr/local/bin/prometheus
COPY --from=prometheus /bin/promtool /usr/local/bin/promtool
COPY --from=prometheus /etc/prometheus/console_libraries /etc/prometheus/console_libraries
COPY --from=prometheus /etc/prometheus/consoles /etc/prometheus/consoles

# ---- Binários e ficheiros base do Grafana ----
COPY --from=grafana /usr/share/grafana /usr/share/grafana
COPY --from=grafana /etc/grafana /etc/grafana

RUN mkdir -p /prometheus /var/lib/grafana /var/log/grafana /var/log/supervisor \
    && chmod -R 777 /prometheus /var/lib/grafana /var/log/grafana

# ---- As tuas configs (edita antes de build, ver comentários dentro) ----
COPY prometheus.yml /etc/prometheus/prometheus.yml
COPY grafana-provisioning/ /etc/grafana/provisioning/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

ENV GF_PATHS_HOME=/usr/share/grafana \
    GF_PATHS_DATA=/var/lib/grafana \
    GF_PATHS_LOGS=/var/log/grafana \
    GF_PATHS_PLUGINS=/var/lib/grafana/plugins \
    GF_PATHS_PROVISIONING=/etc/grafana/provisioning \
    GF_PATHS_CONFIG=/etc/grafana/grafana.ini \
    GF_SECURITY_ADMIN_PASSWORD=admin123

EXPOSE 9090 3000 9273 8008 6343/udp

ENTRYPOINT ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
