FROM bluenviron/mediamtx:1 AS mediamtx

FROM alpine
COPY --from=mediamtx /mediamtx /
COPY rtsp-simple-server.yml /
ENTRYPOINT [ "/mediamtx" ]