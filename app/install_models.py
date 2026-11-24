"""Provision the models included in the Windows installation."""

import json
import sys
import time

import httpx

from .models import URL


DEFAULT_MODELS = ("qwen3.8:27b", "qwen3:4b", "bge-m3:latest")


def installed(client):
    response = client.get("tags")
    response.raise_for_status()
    return {model["name"] for model in response.json()["models"]}


def pull(client, name):
    print(f"Downloading {name} on the Ollama server...", flush=True)
    last_update = 0
    with client.stream("POST", "pull", json={"model": name, "stream": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event.get("error"):
                raise RuntimeError(event["error"])
            if event.get("status") == "success":
                return
            now = time.monotonic()
            if now - last_update >= 5:
                last_update = now
                total = event.get("total")
                progress = f" {100 * event.get('completed', 0) / total:.0f}%" if total else ""
                print(f"  {event.get('status', 'Downloading')}{progress}", flush=True)
    raise RuntimeError(f"Ollama closed the download before confirming {name}.")


def main():
    with httpx.Client(base_url=URL, trust_env=False, timeout=httpx.Timeout(None, connect=15)) as client:
        available = installed(client)
        for name in DEFAULT_MODELS:
            if name in available:
                print(f"Already installed: {name}", flush=True)
                continue
            pull(client, name)
            available = installed(client)
            if name not in available:
                raise RuntimeError(f"{name} is missing from the Ollama model list after download.")
            print(f"Installed: {name}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as exc:
        sys.exit(f"Model setup failed: {exc}")
