"""Adapter registry. To add a vendor: write a VendorAdapter subclass, register it here,
then set `adapter: <kind>` for its robot type in config/warehouse.yaml. Nothing else changes."""
from .base import Bus, VendorAdapter, Wire
from .mavlink import MavlinkAdapter
from .rest import RestAdapter
from .tcpjson import TcpJsonAdapter
from .vda5050 import Vda5050Adapter

REGISTRY = {c.kind: c for c in (Vda5050Adapter, RestAdapter, TcpJsonAdapter, MavlinkAdapter)}


def build_adapters(cfg: dict, bus: Bus) -> dict:
    return {key: REGISTRY[t["adapter"]](key, t, bus) for key, t in cfg["robot_types"].items()}


__all__ = ["Bus", "VendorAdapter", "Wire", "REGISTRY", "build_adapters"]
