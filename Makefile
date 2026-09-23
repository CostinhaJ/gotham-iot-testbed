BUILD_CMD = docker build
ifdef NOCACHE
BUILD_CMD += --no-cache
endif
ifdef PULL
BUILD_CMD += --pull
endif

CONFIG_FILE = iot-sim.config
include $(CONFIG_FILE)


.PHONY: all templates vyosiso clean imagerm

all: buildstatus/DNS buildstatus/certificates buildstatus/NTP \
     buildstatus/mqtt_broker_tls \
     buildstatus/building_monitor \
	 buildstatus/sensor \
     buildstatus/debug_client

templates: Dockerfiles/certificates/Dockerfile Dockerfiles/DNS/dnsmasq.conf
           

vyosiso:
	wget https://github.com/xsaga/gotham-iot-testbed/releases/download/vyos-1.3.0-rc6-artifacts/vyos-1.3.0-rc6-amd64.iso
	mv -v vyos-1.3.0-rc6-amd64.iso $(shell xdg-user-dir DOWNLOAD)
	wget https://github.com/xsaga/gotham-iot-testbed/releases/download/vyos-1.3.0-rc6-artifacts/empty8G.qcow2
	mv -v empty8G.qcow2 $(shell xdg-user-dir DOWNLOAD)

Dockerfiles/certificates/Dockerfile: Dockerfiles/certificates/Dockerfile.template $(CONFIG_FILE)
	sed 's/!PLACEHOLDER-MQTT_TLS_BROKER_CN!/$(MQTT_TLS_BROKER_CN)/g' $< > $@

Dockerfiles/DNS/dnsmasq.conf: Dockerfiles/DNS/dnsmasq.conf.template $(CONFIG_FILE)
	sed -e 's/!PLACEHOLDER-LOCAL_DOMAIN!/$(LOCAL_DOMAIN)/g' $< > $@

buildstatus/DNS: Dockerfiles/DNS/Dockerfile Dockerfiles/DNS/dnsmasq.conf
	$(BUILD_CMD) --file $< --tag iotsim/dns Dockerfiles/DNS
	@touch $@

buildstatus/certificates: Dockerfiles/certificates/Dockerfile
	$(BUILD_CMD) --file $< --tag iotsim/certificates Dockerfiles/certificates
	@touch $@

buildstatus/NTP: Dockerfiles/NTP/Dockerfile Dockerfiles/NTP/chrony.conf
	$(BUILD_CMD) --file $< --tag iotsim/ntp Dockerfiles/NTP
	@touch $@

buildstatus/mqtt_broker_tls: Dockerfiles/iot/mqtt_broker/Dockerfile.tls Dockerfiles/iot/mqtt_broker/mosquitto_tls.conf buildstatus/certificates
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-broker-tls Dockerfiles/iot/mqtt_broker
	@touch $@

buildstatus/building_monitor: Dockerfiles/iot/building_monitor/Dockerfile Dockerfiles/iot/building_monitor/client.py Dockerfiles/iot/building_monitor/openssl-oqs.cnf Dockerfiles/iot/building_monitor/appliances_energy/energydata_complete.csv.xz buildstatus/certificates
	$(BUILD_CMD) --file $< --tag iotsim/building-monitor Dockerfiles/iot/building_monitor
	@touch $@

buildstatus/sensor: Dockerfiles/iot/sensor/Dockerfile Dockerfiles/iot/sensor/coap-client-mod.c Dockerfiles/iot/sensor/sensor.py buildstatus/certificates
	$(BUILD_CMD) --file $< --tag iotsim/sensor Dockerfiles/iot/sensor
	@touch $@

buildstatus/debug_client: Dockerfiles/iot/debug_client/Dockerfile
	$(BUILD_CMD) --file $< --tag iotsim/debug-client Dockerfiles/iot/debug_client
	@touch $@

clean:
	rm -f buildstatus/*
	rm -f Dockerfiles/certificates/Dockerfile
	rm -f Dockerfiles/DNS/dnsmasq.conf

imagerm: clean
	docker image ls | grep "^iotsim/" | awk '{print $$3}' | xargs docker image rm -f