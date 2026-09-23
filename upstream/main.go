package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"log"
	"net"
	"net/http"
	"sync"
	"time"
)

type connectionKey struct{}
type connection struct{ requests uint64 }

type counters struct {
	ConnectionsTotal  uint64 `json:"connections_total"`
	ConnectionsActive int64  `json:"connections_active"`
	RequestsTotal     uint64 `json:"requests_total"`
	RequestsReused    uint64 `json:"requests_reused"`
}

func main() {
	var mu sync.Mutex
	var stats counters

	api := &http.Server{
		Addr:              ":8443",
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
		// Mantém HTTP/1.1 nos dois cenários, sem multiplexação HTTP/2.
		TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){},
		ConnContext: func(ctx context.Context, _ net.Conn) context.Context {
			return context.WithValue(ctx, connectionKey{}, &connection{})
		},
		ConnState: func(_ net.Conn, state http.ConnState) {
			mu.Lock()
			defer mu.Unlock()
			switch state {
			case http.StateNew:
				stats.ConnectionsTotal++
				stats.ConnectionsActive++
			case http.StateClosed:
				stats.ConnectionsActive--
			}
		},
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/work" {
				http.NotFound(w, r)
				return
			}
			conn := r.Context().Value(connectionKey{}).(*connection)
			mu.Lock()
			stats.RequestsTotal++
			if conn.requests > 0 {
				stats.RequestsReused++
			}
			conn.requests++
			mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte("{\"ok\":true}\n"))
		}),
	}

	// Porta separada: healthchecks e coleta não contam como tráfego da API TLS.
	admin := http.NewServeMux()
	admin.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("ok\n"))
	})
	admin.HandleFunc("/stats", func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		snapshot := stats
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(snapshot)
	})
	// Só anuncia saúde depois de abrir a porta HTTPS e carregar o certificado.
	cert, err := tls.LoadX509KeyPair("/tls/server.crt", "/tls/server.key")
	if err != nil {
		log.Fatal(err)
	}
	api.TLSConfig = &tls.Config{Certificates: []tls.Certificate{cert}, MinVersion: tls.VersionTLS12}
	listener, err := net.Listen("tcp", api.Addr)
	if err != nil {
		log.Fatal(err)
	}
	go func() { log.Fatal(http.ListenAndServe(":8081", admin)) }()
	log.Fatal(api.ServeTLS(listener, "", ""))
}
