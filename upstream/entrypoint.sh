#!/bin/sh
set -eu

# Apenas o certificado público da CA é compartilhado. Chaves ficam no upstream.
mkdir -p /tls /ca
if [ ! -s /tls/server.crt ] || [ ! -s /tls/server.key ] || [ ! -s /ca/ca.crt ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj '/CN=Benchmark CA' -keyout /tls/ca.key -out /ca/ca.crt \
        -addext 'basicConstraints=critical,CA:TRUE' 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj '/CN=upstream' \
        -keyout /tls/server.key -out /tls/server.csr 2>/dev/null
    printf 'subjectAltName=DNS:upstream\nextendedKeyUsage=serverAuth\n' > /tls/extensions
    openssl x509 -req -in /tls/server.csr -CA /ca/ca.crt -CAkey /tls/ca.key \
        -CAcreateserial -CAserial /tls/ca.srl -days 3650 \
        -extfile /tls/extensions -out /tls/server.crt 2>/dev/null
    chmod 600 /tls/*.key
    chmod 644 /ca/ca.crt
fi

exec upstream
