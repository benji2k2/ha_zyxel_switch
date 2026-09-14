"""What the integration reads from a switch, independent of Home Assistant.

Port data comes from standard MIBs (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB, LLDP-MIB),
so any managed switch that implements them works. For the device page, model,
serial number and firmware are taken from Zyxel's private tree where the GS1900
series publishes them as plain text, and from ENTITY-MIB otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time

from .snmp import SnmpClient, SnmpError

SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"

ZYXEL_ENTERPRISE = "1.3.6.1.4.1.890"
ZYXEL_FIRMWARE = "1.3.6.1.4.1.890.1.15.3.1.6.0"
ZYXEL_MODEL = "1.3.6.1.4.1.890.1.15.3.1.11.0"
ZYXEL_SERIAL = "1.3.6.1.4.1.890.1.15.3.1.12.0"

ENT_PHYSICAL_CLASS = "1.3.6.1.2.1.47.1.1.1.1.5"
ENT_PHYSICAL_SOFTWARE_REV = "1.3.6.1.2.1.47.1.1.1.1.10"
ENT_PHYSICAL_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11"
ENT_PHYSICAL_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13"
ENT_CLASS_CHASSIS = 3

IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_ADMIN_STATUS = "1.3.6.1.2.1.2.2.1.7"
IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"
LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"

IF_TYPE_ETHERNET = 6
IF_TYPE_LAG = 161

# ifOperStatus values from IF-MIB.
OPER_STATUS = {
    1: "up",
    2: "down",
    3: "testing",
    4: "unknown",
    5: "dormant",
    6: "not_present",
    7: "lower_layer_down",
}
ADMIN_STATUS = {1: "up", 2: "down", 3: "testing"}


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip("\x00").strip()
    return "" if value is None else str(value).strip()


def _int(value: object) -> int | None:
    return value if isinstance(value, int) else None


@dataclass(frozen=True, slots=True)
class SwitchInfo:
    """Identity of the switch, read once when the integration starts."""

    name: str
    model: str
    serial: str | None
    firmware: str | None
    description: str
    is_zyxel: bool


@dataclass(slots=True)
class PortState:
    """One interface at one point in time."""

    index: int
    name: str
    alias: str
    is_lag: bool
    admin: str
    oper: str
    speed_mbps: int
    in_octets: int | None
    out_octets: int | None
    in_errors: int | None
    out_errors: int | None
    mac_count: int = 0
    neighbor: str | None = None

    @property
    def number(self) -> int:
        """Front-panel number: 1-26 for ports, 1-8 for LAGs."""
        digits = "".join(ch for ch in self.name if ch.isdigit())
        return int(digits) if digits else self.index

    @property
    def label(self) -> str:
        """Human readable name, e.g. ``Port 3 (Kueche)``."""
        base = f"LAG {self.number}" if self.is_lag else f"Port {self.number}"
        return f"{base} ({self.alias})" if self.alias else base

    @property
    def is_up(self) -> bool:
        return self.oper == "up"


@dataclass(slots=True)
class SwitchState:
    """Everything that changes, from one poll."""

    monotonic: float
    uptime_seconds: int | None
    mac_count: int
    ports: dict[int, PortState] = field(default_factory=dict)


def rates(
    previous: PortState | None, current: PortState, elapsed: float
) -> tuple[float | None, float | None]:
    """Receive and transmit rate in Mbit/s between two polls.

    ``None`` when there is no previous sample, the interval is not positive, or a
    counter went backwards (switch reboot or counter reset).
    """
    if previous is None or elapsed <= 0:
        return None, None

    def rate(before: int | None, after: int | None) -> float | None:
        if before is None or after is None or after < before:
            return None
        return (after - before) * 8 / elapsed / 1_000_000

    return rate(previous.in_octets, current.in_octets), rate(
        previous.out_octets, current.out_octets
    )


def _column(table: dict[str, object]) -> dict[int, object]:
    """Index a single-column walk by its integer row index."""
    column: dict[int, object] = {}
    for suffix, value in table.items():
        if suffix.isdigit():
            column[int(suffix)] = value
    return column


class ZyxelSwitch:
    """Reads a switch over SNMP. Tuned for, but not limited to, Zyxel."""

    def __init__(self, host: str, community: str, port: int = 161) -> None:
        self._client = SnmpClient(host, community, port)

    def close(self) -> None:
        self._client.close()

    async def async_get_info(self) -> SwitchInfo:
        """Read name, model, serial and firmware."""
        values = await self._client.get([SYS_DESCR, SYS_OBJECT_ID, SYS_NAME])
        if SYS_OBJECT_ID not in values and SYS_DESCR not in values:
            raise SnmpError("agent answered without system information")
        object_id = _text(values.get(SYS_OBJECT_ID))
        is_zyxel = object_id == ZYXEL_ENTERPRISE or object_id.startswith(ZYXEL_ENTERPRISE + ".")
        description = _text(values.get(SYS_DESCR))

        model = serial = firmware = ""
        if is_zyxel:
            private = await self._client.get([ZYXEL_MODEL, ZYXEL_SERIAL, ZYXEL_FIRMWARE])
            model = _text(private.get(ZYXEL_MODEL))
            serial = _text(private.get(ZYXEL_SERIAL))
            # "V2.90(AAHL.2) | 05/07/2026" - the date is the build, not the version.
            firmware = _text(private.get(ZYXEL_FIRMWARE)).split("|")[0].strip()
        if not (model and serial and firmware):
            chassis = await self._async_chassis()
            model = model or chassis.get("model", "")
            serial = serial or chassis.get("serial", "")
            firmware = firmware or chassis.get("firmware", "")

        return SwitchInfo(
            name=_text(values.get(SYS_NAME)) or model or description or "Switch",
            model=model or description or "Switch",
            serial=serial or None,
            firmware=firmware or None,
            description=description,
            is_zyxel=is_zyxel,
        )

    async def _async_chassis(self) -> dict[str, str]:
        """Model, serial and software revision of the first chassis in ENTITY-MIB."""
        try:
            table = await self._client.walk_columns(
                [
                    ENT_PHYSICAL_CLASS,
                    ENT_PHYSICAL_MODEL,
                    ENT_PHYSICAL_SERIAL,
                    ENT_PHYSICAL_SOFTWARE_REV,
                ]
            )
        except SnmpError:
            return {}
        classes = _column(table[ENT_PHYSICAL_CLASS])
        for index in sorted(classes):
            if classes[index] != ENT_CLASS_CHASSIS:
                continue
            return {
                "model": _text(_column(table[ENT_PHYSICAL_MODEL]).get(index)),
                "serial": _text(_column(table[ENT_PHYSICAL_SERIAL]).get(index)),
                "firmware": _text(_column(table[ENT_PHYSICAL_SOFTWARE_REV]).get(index)),
            }
        return {}

    async def async_get_state(self) -> SwitchState:
        """Poll every interface once."""
        columns = (
            IF_TYPE,
            IF_ADMIN_STATUS,
            IF_OPER_STATUS,
            IF_IN_ERRORS,
            IF_OUT_ERRORS,
            IF_NAME,
            IF_HC_IN_OCTETS,
            IF_HC_OUT_OCTETS,
            IF_HIGH_SPEED,
            IF_ALIAS,
            DOT1D_BASE_PORT_IFINDEX,
        )
        scalars = await self._client.get([SYS_UPTIME])
        # The octet counters are what rates are computed from, so the timestamp
        # belongs to this request, not to the slower FDB and LLDP walks after it.
        before = time.monotonic()
        table = await self._client.walk_columns(list(columns))
        sampled = (before + time.monotonic()) / 2
        fdb = await self._client.walk(DOT1Q_TP_FDB_PORT)
        lldp = await self._client.walk(LLDP_REM_SYS_NAME)
        (
            if_type,
            admin,
            oper,
            in_errors,
            out_errors,
            names,
            in_octets,
            out_octets,
            speed,
            alias,
            base_port,
        ) = (_column(table[column]) for column in columns)

        ports: dict[int, PortState] = {}
        for index, kind in if_type.items():
            if kind not in (IF_TYPE_ETHERNET, IF_TYPE_LAG):
                continue
            oper_status = OPER_STATUS.get(_int(oper.get(index)) or 0, "unknown")
            if kind == IF_TYPE_LAG and oper_status == "not_present":
                continue
            ports[index] = PortState(
                index=index,
                name=_text(names.get(index)) or str(index),
                alias=_text(alias.get(index)),
                is_lag=kind == IF_TYPE_LAG,
                admin=ADMIN_STATUS.get(_int(admin.get(index)) or 0, "unknown"),
                oper=oper_status,
                speed_mbps=_int(speed.get(index)) or 0,
                in_octets=_int(in_octets.get(index)),
                out_octets=_int(out_octets.get(index)),
                in_errors=_int(in_errors.get(index)),
                out_errors=_int(out_errors.get(index)),
            )

        # FDB rows are indexed by VLAN and MAC; the value is a bridge port number,
        # which dot1dBasePortIfIndex maps to an interface.
        mac_count = 0
        for value in fdb.values():
            bridge_port = _int(value)
            if not bridge_port:
                continue
            mac_count += 1
            if_index = _int(base_port.get(bridge_port)) or bridge_port
            if if_index in ports:
                ports[if_index].mac_count += 1

        # lldpRemSysName is indexed timeMark.localPortNum.remIndex. On the GS1900
        # the local port number is the interface index.
        for suffix, value in sorted(lldp.items()):
            parts = suffix.split(".")
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            port = ports.get(int(parts[1]))
            name = _text(value)
            if port is not None and name and port.neighbor is None:
                port.neighbor = name

        ticks = _int(scalars.get(SYS_UPTIME))
        return SwitchState(
            monotonic=sampled,
            uptime_seconds=None if ticks is None else ticks // 100,
            mac_count=mac_count,
            ports=dict(sorted(ports.items())),
        )
