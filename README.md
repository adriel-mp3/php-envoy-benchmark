# PHP + Envoy: connection pooling benchmark

Comparação entre chamadas HTTPS feitas diretamente pelo PHP e chamadas encaminhadas
pelo Envoy, que reutiliza conexões com o upstream. Docker Compose, cURL, HTTP/1.1
e `tc netem`, sem framework ou infraestrutura de observabilidade adicional.

**Pergunta do experimento:** em quais condições o custo de passar pelo proxy é
compensado pela reutilização de conexões TCP/TLS?

[Resultados](#resultados) · [Metodologia](#metodologia) · [Como executar](#como-executar) · [Métricas](#consultar-as-métricas)

## Resultados

Apurado de **23/09/2026**: **12 execuções comparáveis**, com **1.000 requests por
execução** e **concorrência 8**. Todas passaram na validação: **12.000 respostas
HTTP 200**, sem falhas de transporte ou de conexão reportadas pelo Envoy.

Neste ambiente, o caminho via Envoy apresentou menor latência e maior throughput
em todos os níveis de atraso testados, inclusive em 0 ms. Isso descreve esta
amostra; ela não identifica uma condição em que o overhead do proxy supere o
benefício do pooling.

Os [dados usados no apurado](benchmark/baseline/2026-09-23.json) estão preservados
no repositório, com o identificador e o resumo de cada execução.

### Latência e throughput

Cada célula representa a **mediana da métrica entre as execuções** daquele
cenário. Há duas execuções por caminho em 0 e 30 ms, e apenas uma em 10 e 50 ms.
As colunas p50/p95/p99 são medianas dos percentis de cada execução, **não percentis
recalculados sobre o conjunto de requests**. Com duas amostras, a mediana é a
média dos dois valores centrais.

| Netem (ms) | Caminho | Execuções | Média (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Requests/s ↑ |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | Direto | 2 | 3,65 | 3,55 | 4,50 | 5,20 | 2.164,44 |
| 0 | Envoy | 2 | 1,65 | 1,60 | 1,90 | 2,80 | 4.877,50 |
| 10 | Direto | 1 | 33,40 | 33,30 | 34,40 | 35,30 | 239,39 |
| 10 | Envoy | 1 | 12,50 | 12,10 | 12,40 | 12,70 | 641,13 |
| 30 | Direto | 2 | 93,60 | 93,55 | 94,55 | 95,65 | 85,46 |
| 30 | Envoy | 2 | 33,15 | 32,15 | 32,45 | 33,20 | 241,29 |
| 50 | Direto | 1 | 153,80 | 153,70 | 154,70 | 155,30 | 52,02 |
| 50 | Envoy | 1 | 54,00 | 52,30 | 52,50 | 53,00 | 148,25 |

Nas colunas de latência, menor é melhor. `Netem` é o atraso adicional na saída
do upstream, em uma única direção; 0 ms significa ausência de atraso artificial,
não ausência de latência de rede. As casas decimais da agregação não aumentam a
resolução original do relatório do hey, de 0,1 ms.

### Diferença entre os caminhos

| Netem (ms) | Throughput Envoy / direto | Redução da latência média |
| ---: | ---: | ---: |
| 0 | 2,25× | 54,8% |
| 10 | 2,68× | 62,6% |
| 30 | 2,82× | 64,6% |
| 50 | 2,85× | 64,9% |

Razão de throughput = `requests/s Envoy ÷ requests/s direto`.
Redução de latência = `1 − média Envoy ÷ média direto`.
Os cálculos usam as medianas antes do arredondamento da tabela.

### Conexões e reutilização

Totais das seis execuções de cada caminho, somando os quatro níveis de atraso:

| Métrica | Direto | Envoy |
| --- | ---: | ---: |
| Requests recebidos no upstream | 6.000 | 6.000 |
| Conexões TCP abertas com o upstream | 6.000 | 48 |
| Novas conexões por lote de 1.000 requests | 1.000 | 8 |
| Requests atendidos em conexão já usada | 0 | 5.953 |
| Percentual de requests reutilizando conexão | 0% | 99,22% |
| Conexões ainda abertas após cada lote | 0 | 8 |

Foram **99,2% menos conexões novas** no caminho via Envoy. O número de conexões
abertas por lote permaneceu em oito nos quatro níveis de atraso medidos.

A reutilização é contada por socket no upstream. Uma conexão que atende dez
requests contribui com nove reutilizações. Na primeira execução Envoy de 0 ms,
houve oito conexões abertas e 993 requests reutilizados: sete conexões atenderam
requests e uma não foi usada durante a medição. Por isso, subtrair conexões de
requests daria uma estimativa diferente da contagem observada.

### Leitura dos dados

- **O custo do atraso cresceu mais no caminho direto.** De 0 para 50 ms de netem,
  a latência média agregada passou de 3,65 para 153,80 ms no direto e de 1,65 para
  54,00 ms via Envoy. O comportamento é compatível com o custo de repetir as
  trocas de TCP/TLS a cada chamada, enquanto sockets reutilizados continuam
  pagando o custo da requisição e da resposta.
- **O pooling também apareceu nos contadores.** A redução de conexões de 1.000
  para oito por lote acompanha a diferença de desempenho. Isso sustenta a
  interpretação de reutilização, mas não separa seu efeito do restante do proxy.
- **O direto não venceu em 0 ms nesta amostra.** Em outro ambiente isso pode
  acontecer: quando estabelecer conexões locais é barato, o salto extra, o
  processamento HTTP e o agendamento do proxy podem dominar a comparação.

## Metodologia

```text
DIRETO
hey ── HTTP ──> PHP /direct ── HTTPS ──> upstream /work

COM ENVOY
hey ── HTTP ──> PHP /envoy ── HTTP ──> Envoy ══ HTTPS reutilizado ══> upstream /work
```

| Parâmetro | Configuração |
| --- | --- |
| Carga | hey 0.1.4; lote fixo de 1.000 requests; oito workers do gerador |
| PHP | Apache com 16 workers; um handle cURL novo por request |
| Envoy | Um worker; pool HTTP/1.1 padrão; sem retries configurados ou cache |
| Upstream | Go, biblioteca padrão; JSON constante de 12 bytes; sem banco ou espera artificial na aplicação |
| Protocolo | HTTP/1.1 em ambos os caminhos; sem multiplexação HTTP/2 |
| TLS | Certificado e nome `upstream` validados por PHP e Envoy usando uma CA local |
| Aquecimento | Nenhum; Envoy reiniciado antes de cada lote, com pool vazio |
| Rede | Containers na mesma rede bridge do Compose; netem apenas na saída do upstream |
| Medição | Latência vista pelo hey, incluindo PHP, filas, rede e upstream |

O PHP destrói o handle cURL ao concluir cada request. Assim, o caminho direto
não mantém um pool persistente entre requests PHP. O upstream aceita keep-alive
nos dois caminhos; a aplicação não força `Connection: close`. No caminho via
Envoy, a conexão PHP → proxy também é nova a cada request, mas o pool do proxy
mantém as conexões com o upstream. O trecho hey → PHP usa keep-alive nos dois testes.

Cada medição usa diferenças entre contadores coletados antes e depois do hey.
Healthchecks e coleta de stats usam uma porta separada e não contam como requests
da API. O pool HTTP/1.1 pode abrir várias conexões conforme a concorrência;
reutilização não significa atender todo o lote em um único socket.

O apurado inclui todos os resumos válidos disponíveis na data com 1.000 requests
e concorrência 8. A execução de validação com 100 requests e concorrência 1 foi
excluída por ter parâmetros diferentes. Uma execução interrompida não produziu
resumo e também não entra no conjunto. A ordem registrada foi direto → Envoy em
cada par; as repetições extras cobrem apenas 0 e 30 ms.

### Ambiente

| Componente | Ambiente observado na apuração |
| --- | --- |
| CPU | AMD Ryzen 5 5600G, seis núcleos / 12 threads |
| Memória disponível ao Docker | 15,41 GiB |
| Sistema | Linux Mint 22.3, kernel 6.17.0-23-generic, x86_64 |
| Docker / Compose | 29.4.3 / 5.1.3 |
| PHP / Apache | 8.4.25 / 2.4.68 |
| Envoy | 1.38.3 |
| Upstream | Build com `golang:1.26-alpine` |
| Limites de recursos | Sem quotas de CPU/memória configuradas no Compose |

Esse inventário foi consultado na mesma máquina durante a apuração. O runner
não registra hardware, utilização de recursos ou versões efetivas por execução.

### Limitações

São uma ou duas execuções por combinação, sem intervalo de confiança e sem
alternância da ordem. Os lotes de 0 ms duraram menos de meio segundo, tornando-os
particularmente sensíveis a ruído. Os valores são descritivos desta amostra,
não uma estimativa robusta de capacidade máxima ou de desempenho em produção.

O teste compara **PHP sem pool persistente na aplicação** com **PHP + pool do
Envoy**. Clientes que reutilizam handles, runtimes persistentes, HTTP/2, retomada
de sessões TLS e payloads maiores podem produzir outros resultados. O segundo
caminho também acrescenta um proxy e muda a implementação TLS; o experimento
não isola exclusivamente o efeito do pooling.

Todos os containers compartilham CPU e rede. Não foram medidos CPU, memória,
perda de pacotes ou o RTT real. O netem modela atraso assimétrico, sem perda,
jitter ou banda configurados. Para uma comparação mais forte, repita os pares
alternando a ordem e explore concorrências e tamanhos de lote diferentes.

## Como executar

Requisitos: Docker com Compose atual (v2 ou posterior), Bash e containers Linux.
O primeiro build precisa de internet. PHP, Go, Python e hey rodam nos containers.

```sh
docker compose up -d
docker compose ps

# Aguarde o PHP ficar healthy antes de testar manualmente
curl http://localhost:8080/direct
curl http://localhost:8080/envoy

./benchmark/run.sh direct
./benchmark/run.sh envoy

./benchmark/run.sh direct 30
./benchmark/run.sh envoy 30
```

O script constrói o container do benchmark, espera os serviços, aplica netem,
reinicia o Envoy, executa hey e imprime o resumo. Execute um teste por vez, sem
tráfego manual durante a medição. Um lock impede dois scripts simultâneos.

```sh
# Reproduzir a matriz básica: um lote por combinação
for ms in 0 10 30 50; do
  ./benchmark/run.sh direct "$ms"
  ./benchmark/run.sh envoy "$ms"
done

# Alterar a carga mantendo parâmetros iguais nos dois caminhos
REQUESTS=4000 CONCURRENCY=8 ./benchmark/run.sh direct 30
REQUESTS=4000 CONCURRENCY=8 ./benchmark/run.sh envoy 30
```

A matriz acima gera oito execuções; o apurado publicado também inclui uma
repetição de cada caminho em 0 e 30 ms. `REQUESTS` deve estar entre 100 e
1.000.000 e ser múltiplo de `CONCURRENCY`, evitando limitações de distribuição
e amostragem do hey. Comece abaixo dos 16 workers do PHP; acima disso, filas
no Apache também entram na latência.

### Arquivos de saída

Cada execução grava `benchmark/results/<data>-<cenário>-<ms>ms/`:

| Arquivo | Conteúdo |
| --- | --- |
| `hey.txt` | Relatório original, histograma, status HTTP e erros |
| `snapshots.json` | Contadores do upstream e do Envoy antes/depois |
| `summary.json` | Parâmetros, latências, throughput, contagens e validação |

Novas execuções ficam fora do Git e não alteram o apurado publicado em
[`benchmark/baseline/2026-09-23.json`](benchmark/baseline/2026-09-23.json).
Esse arquivo preserva os resumos originais selecionados, acrescidos dos IDs;
não contém tempos individuais de cada request.

O script falha se houver HTTP diferente de 200, erros de transporte, contagens
inconsistentes ou métricas ausentes. Compare execuções com **Validação OK**:
o throughput do hey inclui tentativas com erro, enquanto seus percentis incluem
respostas HTTP recebidas, mesmo HTTP 502, e excluem falhas de transporte.

## Latência artificial

O segundo argumento aplica `tc qdisc replace dev eth0 root netem delay <N>ms`
na saída do upstream. Tanto PHP direto quanto Envoy recebem o mesmo atraso nos
pacotes vindos dele, incluindo SYN-ACK, handshake TLS e respostas HTTP.
PHP → Envoy não recebe netem.

`30` significa 30 ms adicionais em uma direção, aproximadamente +30 ms de RTT
sobre a rede local. Handshakes envolvem múltiplas trocas e podem pagar o atraso
mais de uma vez. A coleta de stats também passa por essa interface, mas ocorre
fora do intervalo medido pelo hey.

O script define o atraso em toda execução, inclusive `0`, e remove a regra ao
terminar ou receber Ctrl+C. Apenas o upstream recebe `NET_ADMIN`; nenhum
container usa `privileged`.

```sh
# Inspecionar a regra e possíveis drops durante um teste
docker compose exec upstream tc -s qdisc show dev eth0

# Recuperação após SIGKILL/queda do host; confirme que não há teste rodando
docker compose exec upstream tc qdisc del dev eth0 root
rmdir benchmark/results/.lock
```

`Operation not permitted` indica que é preciso conferir a permissão `NET_ADMIN`.
`Specified qdisc kind is unknown` indica ausência de `sch_netem` no kernel do
host/VM; em Linux, pode ser necessário `sudo modprobe sch_netem`. No Docker
Desktop, o suporte precisa existir na VM Linux. O script falha se não conseguir
aplicar netem, inclusive no teste de 0 ms.

## Consultar as métricas

O admin do Envoy é publicado somente em `127.0.0.1:9901`:

```sh
curl http://localhost:9901/ready
curl 'http://localhost:9901/stats?filter=cluster.api.upstream_cx'
curl 'http://localhost:9901/stats?filter=cluster.api.upstream_rq'
curl 'http://localhost:9901/stats?filter=cluster.api.ssl'

# Contadores comuns aos dois caminhos
docker compose exec upstream wget -qO- http://127.0.0.1:8081/stats
```

| Métrica | Significado |
| --- | --- |
| Envoy `cluster.api.upstream_cx_total` | Conexões criadas com o upstream |
| Envoy `cluster.api.upstream_cx_active` | Conexões abertas agora, inclusive ociosas |
| Envoy `cluster.api.upstream_rq_total` | Requests encaminhados ao upstream |
| Envoy `cluster.api.upstream_cx_connect_fail` | Falhas de conexão |
| Envoy `cluster.api.ssl.handshake` | Handshakes TLS |
| Upstream `connections_total` / `connections_active` | Conexões TCP aceitas na porta HTTPS / ainda abertas |
| Upstream `requests_total` / `requests_reused` | Requests em `/work` / atendidos em socket já usado |

Os contadores são cumulativos desde a inicialização de cada processo. O resumo
usa deltas para os totais e o valor final para conexões abertas. No teste direto,
os deltas do cluster Envoy devem ficar em zero. Conexões ainda em handshake ou
em processo de fechamento podem aparecer na gauge do upstream.

## Encerrar

```sh
docker compose down
# Também apagar a CA e as chaves locais:
docker compose down -v
```

## Referências

- [Connection pooling no Envoy](https://www.envoyproxy.io/docs/envoy/v1.38.3/intro/arch_overview/upstream/connection_pooling)
- [Estatísticas do cluster Envoy](https://www.envoyproxy.io/docs/envoy/v1.38.3/configuration/upstream/cluster_manager/cluster_stats)
- [hey — gerador de carga HTTP](https://github.com/rakyll/hey)
