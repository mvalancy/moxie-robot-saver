#!/usr/bin/env python3
"""Tiny TCP forwarder: <bind>:<listen_port> → 127.0.0.1:<target_port>.

Why this exists: `MoxieRuntime._start_status_server` binds **127.0.0.1** on purpose —
the status/config endpoint is not something to expose by accident. In the one-command
stack the parent console runs in a *different container*, so it cannot reach that
loopback socket. This deploy-layer shim makes the opt-in explicit: it only runs when
`MOXIE_STATUS_PROXY_PORT` is set (the compose file sets it), and it forwards the raw
bytes of any method, so `GET /status`, `GET /telemetry` and `POST /config` all work.

Stdlib only, no deps.  Usage:  python status_proxy.py <listen_port> [target_port] [bind]
"""
import socket
import sys
import threading

BUF = 65536


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(BUF)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _serve_one(client: socket.socket, target: tuple) -> None:
    try:
        upstream = socket.create_connection(target, timeout=10)
    except OSError:
        client.close()
        return
    threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    threading.Thread(target=_pump, args=(upstream, client), daemon=True).start()


def main(argv) -> int:
    listen_port = int(argv[1]) if len(argv) > 1 else 8931
    target_port = int(argv[2]) if len(argv) > 2 else 8930
    bind = argv[3] if len(argv) > 3 else "0.0.0.0"
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind, listen_port))
    srv.listen(64)
    print(f"[status-proxy] {bind}:{listen_port} → 127.0.0.1:{target_port}", flush=True)
    while True:
        try:
            client, _ = srv.accept()
        except OSError:
            break
        _serve_one(client, ("127.0.0.1", target_port))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
