#!/usr/bin/env python3
"""
9:30 NY ICT Strategy Dedicated Journal & Analytics Web App
Single-command launcher script.
"""
import os
import sys
import webbrowser
import threading
import time

def run_server(port: int = 8500):
    try:
        import uvicorn
        from journal_backend import app
    except ImportError as e:
        print(f"Error importing dependencies: {e}")
        print("Please ensure fastapi and uvicorn are installed.")
        sys.exit(1)

    url = f"http://localhost:{port}"
    print("=" * 65)
    print(" [9:30 NY ICT STRATEGY TRADING JOURNAL & ANALYTICS GUI]")
    print("=" * 65)
    print(f" Local Web App running at: {url}")
    print(f" SQLite Database: trading_journal.db")
    print(f" Screenshot Uploads: uploads/screenshots/")
    print("=" * 65)

    def open_browser():
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    port = 8500
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    run_server(port)
