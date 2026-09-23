FROM golang:1.26-alpine AS build
WORKDIR /src
COPY main.go .
RUN CGO_ENABLED=0 go build -trimpath -o /upstream main.go

FROM alpine:3.23
RUN apk add --no-cache iproute2-tc openssl
COPY --from=build /upstream /usr/local/bin/upstream
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["sh", "/usr/local/bin/entrypoint.sh"]
