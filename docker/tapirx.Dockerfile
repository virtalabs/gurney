# Build TapirX from virtalabs/tapirx (for OBJ-2 tapirx-dicom-discovery scenario).
FROM golang:1.23-alpine AS builder
RUN apk add --no-cache git libpcap-dev gcc musl-dev tcpdump
WORKDIR /build
RUN git clone --depth 1 https://github.com/virtalabs/tapirx.git . && \
    go mod init github.com/virtalabs/tapirx && \
    go mod tidy && \
    go build -o tapirx .

FROM alpine:3.19
RUN apk add --no-cache libpcap
COPY --from=builder /build/tapirx /usr/local/bin/
COPY docker/tapirx-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/tapirx-entrypoint.sh
ENTRYPOINT ["tapirx-entrypoint.sh"]
