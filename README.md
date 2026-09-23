# PHP → HTTPS, com e sem pooling do Envoy

Em quais condições reutilizar conexões HTTPS compensa o custo de passar por um
proxy? Este experimento compara uma aplicação PHP que cria um cliente cURL por
request com a mesma aplicação usando o pool de conexões do Envoy. Os resultados
dependem da latência, concorrência, CPU e comportamento do cliente; não há um
vencedor presumido.

## Arquitetura

```text
                            ┌─ /direct ── HTTPS ──────────────────┐
hey ── HTTP ──> PHP/Apache ──┤                                     ├──> upstream /work
                            └─ /envoy ─── HTTP ──> Envoy ── HTTPS ─┘
                                                   pool HTTP/1.1
```

- **PHP 8.4 + Apache:** 16 workers fixos, sem framework. Cada endpoint faz uma
  chamada e devolve o mesmo JSON. Cada request cria e destrói seu próprio handle
  cURL, sem compartilhamento de conexões entre requests PHP. Não enviamos
  `Connection: close` ao upstream: ele aceita keep-alive em ambos os caminhos.
- **Upstream Go:** somente biblioteca padrão; HTTPS em `8443`, resposta pequena e
  constante, sem banco ou espera na aplicação. Conta conexões TCP aceitas e,
  por conexão, quantas requisições já foram atendidas. Saúde e contadores ficam
  em outra porta (`8081`), fora dessas contagens.
- **Envoy:** um worker, cluster `api`, keep-alive e pool padrão; sem retries
  configurados, cache ou limite de uma requisição por conexão. O pool pode abrir
  várias conexões conforme a concorrência.
- **hey:** executado em um container temporário, na mesma rede do Compose.
  O trecho hey → PHP usa keep-alive nos dois testes.

HTTP/1.1 é usado nos dois caminhos, inclusive até o upstream, para observar
pooling sem introduzir multiplexação HTTP/2. PHP e Envoy validam o certificado
e o nome `upstream` usando uma CA local criada automaticamente. Somente o
certificado público da CA é compartilhado; as chaves ficam em um volume do
upstream. Nada usa `-k` ou desabilita a verificação TLS.

## Executar

Requisitos: Docker com uma versão atual do Compose (v2 ou posterior) e Bash. Não é necessário instalar
PHP, Go, Python ou hey no host. O primeiro build precisa de acesso à internet.
Use containers Linux; `tc netem` depende do kernel do host/VM do Docker.

```sh
docker compose up -d
docker compose ps

curl http://localhost:8080/direct
curl http://localhost:8080/envoy

./benchmark/run.sh direct
./benchmark/run.sh envoy

./benchmark/run.sh direct 30
./benchmark/run.sh envoy 30
```

Na primeira subida, aguarde o PHP ficar `healthy` em `docker compose ps`.
O script espera a prontidão antes da medição e constrói automaticamente a imagem
do benchmark na primeira execução.

Por padrão são **1.000 requests, concorrência 8**, sem aquecimento. Cada execução
reinicia o Envoy e começa com pool vazio, incluindo o custo das primeiras
conexões na medição. O script captura contadores antes/depois: chamadas manuais
anteriores não entram nos deltas. Execute um teste por vez, sem tráfego manual
durante a medição; um lock impede dois `run.sh` simultâneos neste diretório.

```sh
# Mesmos parâmetros nos dois cenários
REQUESTS=4000 CONCURRENCY=8 ./benchmark/run.sh direct 30
REQUESTS=4000 CONCURRENCY=8 ./benchmark/run.sh envoy 30

# Matriz de latência
for ms in 0 10 30 50; do
  ./benchmark/run.sh direct "$ms"
  ./benchmark/run.sh envoy "$ms"
done
```

Repita a matriz algumas vezes, alternando a ordem dos cenários. Mantenha iguais
o número de requests e a concorrência; comece abaixo dos 16 workers do PHP.
Acima disso, filas no Apache também entram na latência. O Envoy usa apenas um
worker para deixar o pool fácil de observar, o que também pode limitar throughput.

`REQUESTS` deve estar entre 100 e 1.000.000 e ser múltiplo de `CONCURRENCY`.
Essas restrições evitam percentis ausentes em amostras minúsculas, truncamento
da divisão de requests entre workers e o limite de amostras do hey 0.1.4.

## Resumo e métricas

O script imprime os mesmos campos para os dois cenários:

| Campo | O que observar |
| --- | --- |
| Latência média, p50, p95, p99 (ms) | Tempo visto pelo hey: inclui PHP, filas, rede e upstream |
| Requests por segundo e duração | Throughput e tempo do lote completo |
| Requests planejados, respostas HTTP, HTTP 200, erros | Confirma se o lote foi concluído com sucesso |
| Requests recebidos no upstream (delta) | Total que chegou a `/work` durante o teste |
| Conexões TCP abertas (delta) | Novas conexões aceitas na porta HTTPS durante o lote |
| Conexões ainda abertas | Gauge após o teste; inclui conexões ociosas no pool |
| Requests em conexão já usada (delta e %) | Contagem exata de requests cujo socket já atendeu outro request |
| Envoy: conexões e requests (delta) | Contadores do cluster `api`; devem ficar em zero no teste direto |

Uma conexão que atende 10 requests contribui com 1 conexão e 9 requests
reutilizando conexão. Uma conexão aceita pode ficar ociosa sem uso ou falhar antes de atender um request;
por isso **requests menos conexões não é uma medida exata de reutilização**.
O upstream mede reutilização diretamente e conta apenas sockets HTTPS, incluindo
os que ainda estão em handshake. A gauge de conexões abertas pode incluir sockets
em processo de fechamento no instante da coleta.

Os arquivos de cada execução ficam em `benchmark/results/<data>-<cenário>-<ms>ms/`:

- `hey.txt`: relatório completo original, incluindo histograma e erros.
- `snapshots.json`: contadores antes/depois, sem resetar o upstream.
- `summary.json`: resumo comparável, parâmetros e resultado da validação.

O script termina com erro se houver respostas diferentes de HTTP 200, falhas de
transporte, contagens inconsistentes ou percentis ausentes. Percentis do hey
consideram respostas HTTP recebidas, inclusive HTTP 502, mas não falhas de
transporte; requests/s inclui tentativas que falharam. Compare desempenho apenas
entre execuções com **Validação OK**. A impressão em milissegundos herda o
arredondamento de quatro casas decimais em segundos do relatório do hey.

## Latência artificial com tc netem

O segundo argumento aplica `tc qdisc replace dev eth0 root netem delay <N>ms`
**na saída do container upstream**. Tanto PHP direto quanto Envoy recebem o mesmo
atraso nos pacotes vindos do upstream: SYN-ACK, handshake TLS e respostas HTTP.
O trecho PHP → Envoy não recebe netem. Não é um `sleep` na API.

`30` significa **30 ms adicionais em uma direção**, aproximadamente +30 ms de
RTT em relação à rede local, não 30 ms em cada direção. Handshakes envolvem
múltiplas trocas e podem pagar esse atraso mais de uma vez. A modelagem é
assimétrica e não inclui perda, jitter ou limite de banda. Toda saída `eth0` do
upstream é afetada, incluindo a coleta de stats, feita fora do intervalo do hey.

O script define o atraso em toda execução, inclusive `0`, e remove a regra ao
terminar ou receber Ctrl+C. Só o upstream recebe a capability `NET_ADMIN`;
nenhum container usa `privileged`.

```sh
# Inspecionar a regra e eventuais drops durante um teste
docker compose exec upstream tc -s qdisc show dev eth0

# Recuperação manual, se o script tiver sido morto com SIGKILL ou o host cair
docker compose exec upstream tc qdisc del dev eth0 root
# Só remova o lock depois de confirmar que nenhum teste está rodando:
rmdir benchmark/results/.lock
```

Se aparecer `Operation not permitted`, confira se `NET_ADMIN` é permitido pelo
Docker (ambientes rootless/restritos podem impedi-lo). Se aparecer
`Specified qdisc kind is unknown`, falta `sch_netem` no kernel do host/VM.
Em um host Linux compatível, pode ser necessário `sudo modprobe sch_netem`.
No Docker Desktop, o módulo precisa estar disponível na VM Linux.
O script falha explicitamente se não conseguir aplicar o atraso, inclusive no
teste de 0 ms; não continua alegando uma latência que não foi aplicada.

## Consultar o Envoy e o upstream

O admin do Envoy é publicado somente em `127.0.0.1:9901`:

```sh
curl http://localhost:9901/ready
curl 'http://localhost:9901/stats?filter=cluster.api.upstream_cx'
curl 'http://localhost:9901/stats?filter=cluster.api.upstream_rq'
curl 'http://localhost:9901/stats?filter=cluster.api.ssl'

# Contadores exatos, comuns aos dois caminhos
docker compose exec upstream wget -qO- http://127.0.0.1:8081/stats
```

Observe `cluster.api.upstream_cx_total` (conexões criadas), `upstream_cx_active`
(abertas agora), `upstream_rq_total` (requests) e `upstream_cx_connect_fail`
(falhas de conexão). `cluster.api.ssl.handshake` ajuda a observar handshakes TLS.
Os contadores do Envoy são cumulativos desde a inicialização; o script reinicia
o processo entre testes. A reutilização exata vem de `requests_reused` no
upstream, pois esses contadores do Envoy não equivalem a um contador de reuse.

## Interpretar sem antecipar a conclusão

Sem atraso artificial, estabelecer TCP/TLS na rede local pode ser barato o
suficiente para que a chamada direta vença: o proxy acrescenta um salto,
processamento HTTP e agendamento. Reutilizar sockets, por si só, não garante
latência menor ou mais requests/s.

Conforme cresce o custo das trocas de rede, evitar novos estabelecimentos de
TCP/TLS pode compensar esse overhead. A conexão reaproveitada ainda paga o custo
da requisição e da resposta. O pool HTTP/1.1 atende um request por conexão por
vez, portanto concorrência maior pode exigir mais conexões.

Este teste compara **PHP sem pool persistente na aplicação** com **PHP + pool do
Envoy**. Não representa todo cliente PHP: processos longos que reutilizam um
handle cURL, clientes com pooling e runtimes persistentes podem ter outro
resultado. Também não isola exclusivamente pooling: o segundo caminho adiciona
o proxy e usa outra implementação TLS. HTTP/2, TLS session resumption entre
clientes persistentes, payloads grandes e trabalho real na API mudam o cenário.

Todos os containers competem pela CPU e rede da mesma máquina/VM. CPU saturada,
outros processos, limites de workers, filas, drops no netem e lotes curtos podem
dominar os resultados. Relacione percentis e throughput com erros, conexões e
reutilização; repita antes de atribuir uma diferença ao pooling.

## Encerrar e referências

```sh
docker compose down
# Para também apagar a CA e as chaves locais:
docker compose down -v
```

Detalhes nas fontes: [pooling do Envoy](https://www.envoyproxy.io/docs/envoy/v1.38.3/intro/arch_overview/upstream/connection_pooling),
[estatísticas do cluster](https://www.envoyproxy.io/docs/envoy/v1.38.3/configuration/upstream/cluster_manager/cluster_stats)
e [hey](https://github.com/rakyll/hey).
