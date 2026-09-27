"""Check HTTP service health; USB disconnects remain recoverable in the poller."""
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=5) as response:
    if response.status != 200:
        raise SystemExit(1)
