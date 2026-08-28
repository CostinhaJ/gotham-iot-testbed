BUILD_CMD = docker build
ifdef NOCACHE
BUILD_CMD += --no-cache
endif

CONFIG_FILE = iot-sim.config
include $(CONFIG_FILE)


.PHONY: all templates vyosiso clean imagerm

all: buildstatus/DNS buildstatus/certificates buildstatus/NTP \
     buildstatus/mqtt_broker_1.6 buildstatus/mqtt_broker_1.6_auth buildstatus/mqtt_broker_tls \
     buildstatus/mqtt_client_t1 buildstatus/mqtt_client_t2 \
     buildstatus/building_monitor buildstatus/ip_camera_street \
     buildstatus/ip_camera_museum buildstatus/stream_server buildstatus/stream_consumer \
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

buildstatus/mqtt_broker_1.6: Dockerfiles/iot/mqtt_broker/Dockerfile.1.6
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-broker-1.6 Dockerfiles/iot/mqtt_broker
	@touch $@

buildstatus/mqtt_broker_1.6_auth: Dockerfiles/iot/mqtt_broker/Dockerfile.1.6.auth Dockerfiles/iot/mqtt_broker/mosquitto_1.6.auth.conf Dockerfiles/iot/mqtt_broker/mosquitto_1.6.auth.passwd
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-broker-1.6-auth Dockerfiles/iot/mqtt_broker
	@touch $@

buildstatus/mqtt_broker_tls: Dockerfiles/iot/mqtt_broker/Dockerfile.tls Dockerfiles/iot/mqtt_broker/mosquitto_tls.conf buildstatus/certificates
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-broker-tls Dockerfiles/iot/mqtt_broker
	@touch $@

buildstatus/mqtt_client_t1: Dockerfiles/iot/mqtt_client_t1/Dockerfile Dockerfiles/iot/mqtt_client_t1/client.py
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-client-t1 Dockerfiles/iot/mqtt_client_t1
	@touch $@

buildstatus/mqtt_client_t2: Dockerfiles/iot/mqtt_client_t2/Dockerfile Dockerfiles/iot/mqtt_client_t2/client.py
	$(BUILD_CMD) --file $< --tag iotsim/mqtt-client-t2 Dockerfiles/iot/mqtt_client_t2
	@touch $@

buildstatus/building_monitor: Dockerfiles/iot/building_monitor/Dockerfile Dockerfiles/iot/building_monitor/client.py Dockerfiles/iot/building_monitor/appliances_energy/energydata_complete.csv.xz buildstatus/certificates
	$(BUILD_CMD) --file $< --tag iotsim/building-monitor Dockerfiles/iot/building_monitor
	@touch $@

buildstatus/ip_camera_street: Dockerfiles/iot/ip_camera/Dockerfile.720_15fps_noaudio Dockerfiles/iot/ip_camera/street_london_rainy_night.mp4 Dockerfiles/iot/ip_camera/ip_camera.py
	$(BUILD_CMD) --file $< --tag iotsim/ip-camera-street Dockerfiles/iot/ip_camera
	@touch $@

buildstatus/ip_camera_museum: Dockerfiles/iot/ip_camera/Dockerfile.720_grayscale_25fps_noaudio Dockerfiles/iot/ip_camera/museum_lebanon.mp4 Dockerfiles/iot/ip_camera/ip_camera.py
	$(BUILD_CMD) --file $< --tag iotsim/ip-camera-museum Dockerfiles/iot/ip_camera
	@touch $@	

buildstatus/stream_server: Dockerfiles/iot/stream_server/Dockerfile Dockerfiles/iot/stream_server/rtsp-simple-server.yml
	$(BUILD_CMD) --file $< --tag iotsim/stream-server Dockerfiles/iot/stream_server
	@touch $@

buildstatus/stream_consumer: Dockerfiles/iot/stream_consumer/Dockerfile Dockerfiles/iot/stream_consumer/consume.py
	$(BUILD_CMD) --file $< --tag iotsim/stream-consumer Dockerfiles/iot/stream_consumer
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