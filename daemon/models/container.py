from __future__ import annotations
from pydantic import BaseModel


class ContainerInfo(BaseModel):
    name: str
    status: str
    image: str = ""
    ports: dict[str, int | None] = {}
    cpu_percent: float = 0.0
    memory_mb: float = 0.0


class SystemMetrics(BaseModel):
    gpu_utilization: int = 0
    gpu_memory_used_mb: int = 0
    gpu_memory_total_mb: int = 0
    gpu_temperature: int = 0
    # Degrees left before the GPU's own thermal limit (nvidia-smi's T.Limit),
    # and whether it is slowing its clocks right now because it is too hot.
    gpu_temp_headroom: int | None = None
    gpu_thermal_slowdown: bool = False
    # The hottest of the board's ACPI thermal zones and the critical trip
    # point at which the firmware powers the machine off.
    soc_temperature: int | None = None
    soc_critical: int | None = None
    gpu_name: str = ""
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
