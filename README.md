<p align="center">
  <img src="custom_components/zyxel_switch/brand/icon.png" width="128" alt="Zyxel Switch" />
</p>

<h1 align="center">Zyxel Switch — Home Assistant Integration</h1>

<p align="center">
  Port status, link speed, traffic, LLDP neighbours and MAC counts of a managed
  <b>Zyxel switch</b> over SNMP — one fast poll, set up in the UI, no YAML.
</p>

<p align="center">
  <a href="README.de.md">Deutsch</a>
</p>

---

## Why

Home Assistant's built-in `snmp` sensor platform creates one YAML sensor per OID,
each with its own SNMP session. For a 24-port switch that means dozens of
sensors, template helpers to make them readable, no device, and a slow start —
on the switch this integration was built with, the `snmp` platform took
**18 seconds** to set up.

This integration reads the whole switch in **one poll of about half a second**
(roughly 15 UDP round trips, table columns fetched together with GetBulk) and
creates a proper device with all ports as entities.

## What you get

A device with model, serial number and firmware, and for every Ethernet port
and every active link aggregation group (LAG):

| Entity | Default | Notes |
|---|---|---|
| `binary_sensor` … **link** | enabled | On when the port is up. Attributes: interface name, port description, admin/oper status, speed, MAC count, LLDP neighbour |
| `sensor` … **speed** | enabled | Negotiated speed in Mbit/s, 0 when down |
| `sensor` … **receive** / **transmit** | enabled | Throughput in Mbit/s, averaged over the polling interval (64-bit counters) |
| `sensor` … **receive errors** / **transmit errors** | disabled | Counters, diagnostic |
| `sensor` … **MAC addresses** | disabled | Devices learned on the port |
| `sensor` … **LLDP neighbor** | disabled | System name of the device on the other end, if it speaks LLDP |

Plus for the switch itself: **Ports connected**, **MAC addresses** and
**Last restart**.

Entity names use the port number and the description configured on the
switch, e.g. *Port 3 (Kitchen) receive*. When the switch restarts, firmware and
model on the device page are refreshed; when ports or LAGs are added, the
integration reloads itself and creates their entities.

Everything is **read-only**. The integration never writes to the switch.

## Requirements

- A managed switch with **SNMP v2c** enabled and a **read-only community**.
- UDP port 161 of the switch reachable from Home Assistant. If the switch only
  accepts management traffic from certain hosts, allow the Home Assistant
  address.
- Home Assistant **2025.2** or newer. Local brand icons need **2026.3**.

No Python packages are installed: the SNMP client is part of the integration.

## Tested with

- Zyxel **GS1900-24**, firmware V2.90(AAHL.2)

Other Zyxel switches of the GS1900 series should work unchanged. Port data
comes from standard MIBs (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB, LLDP-MIB), so other
managed switches will most likely work as well; model, serial number and
firmware then come from ENTITY-MIB. Reports are welcome.

## Installation

### HACS

1. HACS → ⋮ → **Custom repositories**
2. Repository `https://github.com/benji2k2/ha_zyxel_switch`, type **Integration**
3. Download **Zyxel Switch** and restart Home Assistant

### Manually

Copy `custom_components/zyxel_switch` into `/config/custom_components/` and
restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Zyxel Switch**

| Field | |
|---|---|
| Host | IP address or host name of the switch |
| SNMP community | The read-only community, not the login password |
| SNMP port | Usually 161 |

The connection can be changed later with **Reconfigure**; the integration
refuses if the new address belongs to a different switch.

**Options:** polling interval, 10–600 s, default 30 s. Throughput is averaged
over this interval, so shorter intervals show short peaks better.

## Coming from `snmp` YAML sensors

Entity IDs are new. After setting up the integration, remove the old `snmp`
sensors and any template helpers built on them, and update automations and
dashboards to the new entities. If you want to keep an old entity ID, delete
the old entity first and then rename the new one in its settings.

## Troubleshooting

**"The switch did not answer"** almost always means SNMP is disabled, the
community is wrong, or the switch does not accept SNMP from Home Assistant's
address. You can test from any machine on the network:

```bash
snmpget -v2c -c <community> <switch-ip> 1.3.6.1.2.1.1.1.0
```

`tools/probe.py` reads a switch with the integration's own code, outside Home
Assistant, and prints what it would create:

```bash
python tools/probe.py <switch-ip> <community>
```

**Diagnostics** (device page → ⋮ → Download diagnostics) contain every port as
the integration sees it. The community and serial number are redacted.

## Not included

- Writing to the switch (enabling/disabling ports, PoE control)
- Temperature, fan and CPU values — the GS1900 does not expose them in a
  documented form over SNMP
- SNMP v3

## Development

```bash
pip install pytest-homeassistant-custom-component ruff
python -m pytest
ruff check . && ruff format --check .
```

The tests run a small SNMP agent on localhost, so the client, the table parsing
and the Home Assistant setup are exercised end to end without a switch.

## Icons

The icons live in `custom_components/zyxel_switch/brand/`. Home Assistant
picks them up from there from **2026.3** onwards.

This project is not affiliated with or endorsed by Zyxel. Zyxel and the Zyxel
logo are trademarks of Zyxel Networks and are used only to identify the
supported hardware.

## License

MIT
