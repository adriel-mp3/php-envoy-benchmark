#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

mode=${1:-}
delay=${2:-0}
requests=${REQUESTS:-1000}
concurrency=${CONCURRENCY:-8}
if [[ $# -gt 2 || ! $mode =~ ^(direct|envoy)$ || ! $delay =~ ^[0-9]{1,3}$ ]]; then
    echo "Uso: $0 {direct|envoy} [atraso_ms: 0..999]" >&2
    exit 1
fi
delay=$((10#$delay))
if [[ ! $requests =~ ^[1-9][0-9]{0,6}$ || ! $concurrency =~ ^[1-9][0-9]{0,5}$ ]] ||
   (( requests < 100 || requests > 1000000 || concurrency > requests || requests % concurrency != 0 )); then
    echo 'Use REQUESTS entre 100 e 1000000, CONCURRENCY >= 1 e REQUESTS múltiplo de CONCURRENCY.' >&2
    exit 1
fi

mkdir -p benchmark/results
# Evita dois scripts alterando a latência e os contadores ao mesmo tempo.
if ! mkdir benchmark/results/.lock 2>/dev/null; then
    echo 'Já existe um teste em execução (benchmark/results/.lock).' >&2
    exit 1
fi
netem_set=0
cleanup() {
    status=$?
    trap - EXIT
    if (( netem_set )); then
        if ! docker compose exec -T upstream tc qdisc del dev eth0 root; then
            echo 'Falha ao remover netem. Execute: docker compose exec upstream tc qdisc del dev eth0 root' >&2
            status=1
        fi
    fi
    rmdir benchmark/results/.lock
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Também verifica que o Docker/serviços estão disponíveis antes de alterar a rede.
docker compose exec -T upstream wget -q -O /dev/null http://127.0.0.1:8081/health
echo "Cenário=$mode | netem=${delay}ms na saída do upstream | requests=$requests | concorrência=$concurrency"
if ! docker compose exec -T upstream tc qdisc replace dev eth0 root netem delay "${delay}ms"; then
    echo 'tc netem indisponível. Verifique NET_ADMIN e o módulo sch_netem no kernel do Docker.' >&2
    exit 1
fi
netem_set=1

# Pool frio em toda execução, inclusive quando a anterior usou /envoy.
docker compose restart envoy
echo 'Preparando o container de benchmark e executando hey...'
docker compose --progress quiet run --build --rm --no-deps -T --user "$(id -u):$(id -g)" \
    benchmark "$mode" "$delay" "$requests" "$concurrency"
