"""Mochi Chat server (modular version). Standard library only, raw sockets.

    py main.py                         listen on 0.0.0.0:5000, log to ../logs/server.log
    py main.py --port 6000 --verbose   other port, print every SEND/RECV on the console too
    py main.py --no-log                console only
"""
import argparse
import socket
import threading
from pathlib import Path

import config
import logger
from connection import handle_connection


def parse_args():
    p = argparse.ArgumentParser(description="Mochi Chat server")
    p.add_argument("--host", default=config.HOST, help="interface to listen on (default: all)")
    p.add_argument("--port", type=int, default=config.PORT)
    p.add_argument("--log", type=Path, default=config.LOG_FILE, help="log file path")
    p.add_argument("--no-log", action="store_true", help="do not write a log file")
    p.add_argument("--verbose", action="store_true", help="print every message sent/received")
    a = p.parse_args()
    config.HOST, config.PORT, config.VERBOSE = a.host, a.port, a.verbose
    config.LOG_FILE = None if a.no_log else a.log


def lan_ip():
    """The IP other hosts should use. 'Connecting' a UDP socket sends nothing; it only makes the OS
    pick the outgoing interface, whose address we then read."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main():
    parse_args()
    if not (config.WEB_DIR / "index.html").is_file():
        raise SystemExit(f"index.html not found in {config.WEB_DIR}")
    logger.setup()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # IPv4 + TCP
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # restart without "address already in use"
    srv.bind((config.HOST, config.PORT))
    srv.listen(50)  # up to 50 connections may wait to be accepted
    logger.log(f"serving {config.WEB_DIR} | log file: {config.LOG_FILE or 'off'}")
    logger.log(f"open http://localhost:{config.PORT} here, or http://{lan_ip()}:{config.PORT} from other hosts")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle_connection, args=(conn, addr), daemon=True).start()  # one thread per client
    except KeyboardInterrupt:
        logger.log("stopped")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
