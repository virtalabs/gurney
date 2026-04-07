# Build TapirX from local source (for testing feature branches).
FROM golang:1.26-alpine AS builder
RUN apk add --no-cache git libpcap-dev gcc musl-dev
WORKDIR /build
COPY . .
RUN if [ ! -f go.mod ]; then go mod init github.com/virtalabs/tapirx; fi && \
    go mod tidy && go build -o tapirx .

FROM alpine:3.19
RUN apk add --no-cache libpcap
COPY --from=builder /build/tapirx /usr/local/bin/
COPY tapirx-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/tapirx-entrypoint.sh
ENTRYPOINT ["tapirx-entrypoint.sh"]
