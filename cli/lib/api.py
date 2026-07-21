



#!/usr/bin/env python3

import requests

BASE_URL = "http://127.0.0.1:8000"


def get_system():
    r = requests.get(f"{BASE_URL}/api/system", timeout=5)
    r.raise_for_status()
    return r.json()


def get_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=5)
    r.raise_for_status()
    return r.json()
