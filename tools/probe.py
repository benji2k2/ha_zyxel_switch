"""Read a switch with the integration's SNMP code, outside Home Assistant.

    python tools/probe.py <switch-ip> <community>

Only reads. Prints what the integration would see and how long it took.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "zyxel_switch"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"zyxel_switch.{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


async def main(host: str, community: str) -> None:
    sys.modules.setdefault("zyxel_switch", type(sys)("zyxel_switch")).__path__ = [str(ROOT)]
    _load("snmp")
    switch = _load("device")
    client = switch.ZyxelSwitch(host, community)
    try:
        started = time.perf_counter()
        info = await client.async_get_info()
        info_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        first = await client.async_get_state()
        state_ms = (time.perf_counter() - started) * 1000
        await asyncio.sleep(5)
        second = await client.async_get_state()
    finally:
        client.close()

    print(f"info  {info_ms:6.0f} ms  {info}")
    print(f"state {state_ms:6.0f} ms  uptime={second.uptime_seconds}s  macs={second.mac_count}")
    for index, port in second.ports.items():
        rx, tx = switch.rates(first.ports.get(index), port, second.monotonic - first.monotonic)
        print(
            f"  {port.label:34} {port.oper:10} {port.speed_mbps:5} Mbit/s "
            f"rx={_fmt(rx):>8} tx={_fmt(tx):>8} "
            f"err={port.in_errors}/{port.out_errors} macs={port.mac_count} lldp={port.neighbor}"
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
