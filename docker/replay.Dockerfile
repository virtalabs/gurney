# Pcap replay sidecar for OBJ-2 tapirx-dicom-discovery (tcpreplay).
FROM alpine:3.21
RUN apk add --no-cache tcpreplay curl
ENTRYPOINT ["tcpreplay"]
