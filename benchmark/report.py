"""Executa hey, compara snapshots e guarda a saída original, sem dependências Python."""
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODE, DELAY, REQUESTS, CONCURRENCY = sys.argv[1:]
REQUESTS, CONCURRENCY = int(REQUESTS), int(CONCURRENCY)
UPSTREAM = "http://upstream:8081/stats"
ENVOY = "http://envoy:9901"
ENVOY_METRICS = ["upstream_cx_total", "upstream_cx_active", "upstream_rq_total",
                 "upstream_cx_connect_fail", "upstream_rq_5xx"]


def get(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.read().decode()


def snapshot():
    stats = json.loads(get(ENVOY + "/stats?format=json&filter=^cluster\\.api\\."))
    envoy = {s["name"].removeprefix("cluster.api."): s["value"]
             for s in stats["stats"] if "value" in s}
    return {"upstream": json.loads(get(UPSTREAM)),
            "envoy": {name: envoy.get(name, 0) for name in ENVOY_METRICS}}


# Não aquece /work: preserva o custo inicial do pool na medição.
for attempt in range(60):
    try:
        get(ENVOY + "/ready")
        get("http://php/health")
        if json.loads(get(UPSTREAM))["connections_active"] == 0:
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.5)
else:
    sys.exit("Serviços indisponíveis ou conexões anteriores ainda abertas. Consulte docker compose logs.")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
directory = Path("/results") / f"{stamp}-{MODE}-{DELAY}ms"
directory.mkdir()
before = snapshot()
command = ["hey", "-n", str(REQUESTS), "-c", str(CONCURRENCY), "-t", "10",
           "-disable-compression", "-disable-redirects", f"http://php/{MODE}"]
result = subprocess.run(command, capture_output=True, text=True)
(directory / "hey.txt").write_text(result.stdout + result.stderr)
after = snapshot()
(directory / "snapshots.json").write_text(json.dumps({"before": before, "after": after}, indent=2))
if result.returncode:
    sys.exit(f"hey falhou: {result.stderr}; veja {directory.name}/hey.txt")


def number(pattern, scale=1):
    match = re.search(pattern, result.stdout, re.MULTILINE)
    return round(float(match[1]) * scale, 3) if match else None


status_codes = {code: int(count) for code, count in
                re.findall(r"^\s*\[(\d{3})\]\s+(\d+) responses", result.stdout, re.MULTILINE)}
responses = sum(status_codes.values())
upstream = {key: after["upstream"][key] - before["upstream"][key]
            for key in ("connections_total", "requests_total", "requests_reused")}
envoy = {key: after["envoy"][key] - before["envoy"][key]
         for key in ENVOY_METRICS if key != "upstream_cx_active"}
summary = {
    "mode": MODE, "netem_ms": int(DELAY), "concurrency": CONCURRENCY,
    "requests_planned": REQUESTS, "http_responses": responses,
    "http_200": status_codes.get("200", 0), "transport_errors": REQUESTS - responses,
    "http_status_codes": status_codes,
    "duration_s": number(r"^\s*Total:\s+([\d.]+)"),
    "requests_per_second": number(r"^\s*Requests/sec:\s+([\d.]+)"),
    "mean_ms": number(r"^\s*Average:\s+([\d.]+)", 1000),
    **{f"p{p}_ms": number(rf"^\s*{p}% in ([\d.]+)", 1000) for p in (50, 95, 99)},
    "upstream_delta": upstream,
    "upstream_connections_active_after": after["upstream"]["connections_active"],
    "envoy_delta": envoy,
    "envoy_connections_active_after": after["envoy"]["upstream_cx_active"],
}
valid = (summary["http_200"] == REQUESTS and upstream["requests_total"] == REQUESTS
         and all(summary[key] is not None for key in
                 ("duration_s", "requests_per_second", "mean_ms", "p50_ms", "p95_ms", "p99_ms"))
         and all(value >= 0 for value in upstream.values())
         and 0 <= upstream["requests_reused"] <= upstream["requests_total"]
         and envoy["upstream_cx_connect_fail"] == 0
         and envoy["upstream_rq_total"] == (REQUESTS if MODE == "envoy" else 0))
summary["valid"] = valid
(directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")


def row(label, value):
    print(f"{label:<42} {value}")


print()
row("Cenário / netem saída upstream", f"{MODE} / {DELAY} ms")
row("Concorrência / requests planejados", f"{CONCURRENCY} / {REQUESTS}")
row("Respostas HTTP / HTTP 200 / erros transporte", f"{responses} / {summary['http_200']} / {summary['transport_errors']}")
row("Duração (s) / requests por segundo", f"{summary['duration_s']} / {summary['requests_per_second']}")
row("Latência média / p50 / p95 / p99 (ms)", " / ".join(str(summary[k]) for k in ("mean_ms", "p50_ms", "p95_ms", "p99_ms")))
row("Requests recebidos no upstream (delta)", upstream["requests_total"])
row("Conexões TCP abertas no upstream (delta)", upstream["connections_total"])
row("Conexões ainda abertas no upstream", after["upstream"]["connections_active"])
reused = upstream["requests_reused"]
fraction = reused / upstream["requests_total"] if upstream["requests_total"] else 0
row("Requests em conexão já usada (delta)", f"{reused} ({fraction:.1%})")
row("Envoy: conexões / requests (delta)", f"{envoy['upstream_cx_total']} / {envoy['upstream_rq_total']}")
row("Envoy: conexões ainda abertas", after["envoy"]["upstream_cx_active"])
row("Validação", "OK" if valid else "FALHOU: confira hey.txt e snapshots.json")
print(f"\nArquivos: benchmark/results/{directory.name}/")
if not valid:
    sys.exit(1)
