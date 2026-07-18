from __future__ import annotations

import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any

import docker
import psutil
from docker.errors import DockerException
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(
    title="NUTTZ Core API",
    description="System management API for NUTTZ OS.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost",
        "http://127.0.0.1",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def bytes_to_gib(value: int | float) -> float:
    return round(float(value) / (1024**3), 2)


def format_uptime(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)

    return f"{days}d {hours}h {minutes}m"


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "NUTTZ Core API",
        "version": "0.2.0",
        "status": "online",
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/system")
def system_status() -> dict[str, Any]:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot_time = psutil.boot_time()
    uptime_seconds = time.time() - boot_time

    return {
        "hostname": socket.gethostname(),
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "cpu": {
            "usage_percent": psutil.cpu_percent(interval=0.5),
            "physical_cores": psutil.cpu_count(logical=False),
            "logical_cores": psutil.cpu_count(logical=True),
        },
        "memory": {
            "usage_percent": memory.percent,
            "total_gib": bytes_to_gib(memory.total),
            "used_gib": bytes_to_gib(memory.used),
            "available_gib": bytes_to_gib(memory.available),
        },
        "storage": {
            "usage_percent": disk.percent,
            "total_gib": bytes_to_gib(disk.total),
            "used_gib": bytes_to_gib(disk.used),
            "free_gib": bytes_to_gib(disk.free),
        },
        "uptime": {
            "seconds": int(uptime_seconds),
            "formatted": format_uptime(uptime_seconds),
            "boot_time": datetime.fromtimestamp(
                boot_time,
                tz=timezone.utc,
            ).isoformat(),
        },
    }


@app.get("/api/docker")
def docker_status() -> dict[str, Any]:
    try:
        client = docker.from_env()
        client.ping()

        all_containers = client.containers.list(all=True)
        running_containers = [
            container
            for container in all_containers
            if container.status == "running"
        ]

        containers = []

        for container in all_containers:
            image_name = (
                container.image.tags[0]
                if container.image.tags
                else container.image.short_id
            )

            containers.append(
                {
                    "id": container.short_id,
                    "name": container.name,
                    "image": image_name,
                    "status": container.status,
                }
            )

        return {
            "status": "online",
            "running": len(running_containers),
            "total": len(all_containers),
            "containers": containers,
        }

    except DockerException as error:
        return {
            "status": "offline",
            "running": 0,
            "total": 0,
            "containers": [],
            "error": str(error),
        }
