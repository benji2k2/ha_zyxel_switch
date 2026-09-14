<p align="center">
  <img src="custom_components/zyxel_switch/brand/icon.png" width="128" alt="Zyxel Switch" />
</p>

<h1 align="center">Zyxel Switch — Home-Assistant-Integration</h1>

<p align="center">
  Portstatus, Geschwindigkeit, Durchsatz, LLDP-Nachbarn und MAC-Adressen eines
  verwalteten <b>Zyxel-Switches</b> per SNMP — eine schnelle Abfrage, Einrichtung
  über die Oberfläche, kein YAML.
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

---

## Warum

Die eingebaute `snmp`-Sensorplattform von Home Assistant legt pro OID einen
YAML-Sensor an, jeder mit eigener SNMP-Sitzung. Bei einem 24-Port-Switch sind
das Dutzende Sensoren, Template-Helfer, damit man sie lesen kann, kein Gerät und
ein langsamer Start — beim Switch, mit dem diese Integration entstanden ist,
brauchte die `snmp`-Plattform **18 Sekunden** zum Einrichten.

Diese Integration liest den ganzen Switch in **einer Abfrage von etwa einer
halben Sekunde** (rund 15 UDP-Anfragen, Tabellenspalten gebündelt per GetBulk)
und legt ein richtiges Gerät mit allen Ports als Entitäten an.

## Was du bekommst

Ein Gerät mit Modell, Seriennummer und Firmware, und für jeden Ethernet-Port und
jede aktive Portbündelung (LAG):

| Entität | Standard | Hinweis |
|---|---|---|
| `binary_sensor` … **Verbindung** | aktiv | An, wenn der Port verbunden ist. Attribute: Schnittstelle, Portbeschreibung, Admin-/Betriebsstatus, Geschwindigkeit, MAC-Anzahl, LLDP-Nachbar |
| `sensor` … **Geschwindigkeit** | aktiv | Ausgehandelte Geschwindigkeit in Mbit/s, 0 wenn getrennt |
| `sensor` … **Empfang** / **Senden** | aktiv | Durchsatz in Mbit/s, gemittelt über das Abfrageintervall (64-Bit-Zähler) |
| `sensor` … **Empfangsfehler** / **Sendefehler** | deaktiviert | Zähler, Diagnose |
| `sensor` … **MAC-Adressen** | deaktiviert | Am Port gelernte Geräte |
| `sensor` … **LLDP-Nachbar** | deaktiviert | Systemname des Geräts am anderen Ende, sofern es LLDP spricht |

Dazu für den Switch selbst: **Verbundene Ports**, **MAC-Adressen** und
**Letzter Neustart**.

Die Entitätsnamen enthalten die Portnummer und die am Switch hinterlegte
Beschreibung, z. B. *Port 3 (Küche) Empfang*. Nach einem Neustart des Switches
werden Firmware und Modell auf der Geräteseite aktualisiert; kommen Ports oder
LAGs hinzu, lädt sich die Integration neu und legt deren Entitäten an.

Alles ist **nur lesend**. Die Integration schreibt nie auf den Switch.

## Voraussetzungen

- Ein verwalteter Switch mit aktiviertem **SNMP v2c** und einer
  **Lese-Community**.
- UDP-Port 161 des Switches von Home Assistant aus erreichbar. Nimmt der Switch
  Verwaltungszugriffe nur von bestimmten Rechnern an, die Adresse von Home
  Assistant freigeben.
- Home Assistant **2025.2** oder neuer. Die eigenen Icons erscheinen ab **2026.3**.

Es werden keine Python-Pakete installiert: Der SNMP-Client ist Teil der
Integration.

## Getestet mit

- Zyxel **GS1900-24**, Firmware V2.90(AAHL.2)

Andere Zyxel-Switches der GS1900-Serie sollten unverändert funktionieren. Die
Portdaten stammen aus Standard-MIBs (IF-MIB, BRIDGE-MIB, Q-BRIDGE-MIB,
LLDP-MIB), andere verwaltete Switches funktionieren daher sehr wahrscheinlich
ebenfalls; Modell, Seriennummer und Firmware kommen dann aus ENTITY-MIB.
Rückmeldungen sind willkommen.

## Installation

### HACS

1. HACS → ⋮ → **Benutzerdefinierte Repositories**
2. Repository `https://github.com/benji2k2/ha_zyxel_switch`, Typ **Integration**
3. **Zyxel Switch** herunterladen und Home Assistant neu starten

### Von Hand

`custom_components/zyxel_switch` nach `/config/custom_components/` kopieren und
Home Assistant neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Zyxel Switch**

| Feld | |
|---|---|
| Host | IP-Adresse oder Hostname des Switches |
| SNMP-Community | Die Lese-Community, nicht das Anmeldepasswort |
| SNMP-Port | In der Regel 161 |

Die Verbindung lässt sich später über **Neu konfigurieren** ändern; zeigt die
neue Adresse auf einen anderen Switch, lehnt die Integration ab.

**Optionen:** Abfrageintervall, 10–600 s, Standard 30 s. Der Durchsatz wird über
dieses Intervall gemittelt, kürzere Intervalle zeigen kurze Spitzen also besser.

## Umstieg von `snmp`-YAML-Sensoren

Die Entitäts-IDs sind neu. Nach der Einrichtung die alten `snmp`-Sensoren und
darauf aufbauende Template-Helfer entfernen und Automationen sowie Dashboards
auf die neuen Entitäten umstellen. Soll eine alte Entitäts-ID erhalten bleiben,
zuerst die alte Entität löschen und dann die neue in ihren Einstellungen
umbenennen.

## Fehlersuche

**„Der Switch antwortet nicht"** heißt fast immer: SNMP ist aus, die Community
stimmt nicht, oder der Switch nimmt SNMP von der Adresse von Home Assistant
nicht an. Testen lässt sich das von jedem Rechner im Netz:

```bash
snmpget -v2c -c <community> <switch-ip> 1.3.6.1.2.1.1.1.0
```

`tools/probe.py` liest einen Switch mit dem Code der Integration, außerhalb von
Home Assistant, und zeigt, was sie anlegen würde:

```bash
python tools/probe.py <switch-ip> <community>
```

**Diagnosedaten** (Geräteseite → ⋮ → Diagnosedaten herunterladen) enthalten
jeden Port so, wie die Integration ihn sieht. Community und Seriennummer sind
geschwärzt.

## Nicht enthalten

- Schreiben auf den Switch (Ports ein-/ausschalten, PoE steuern)
- Temperatur, Lüfter und CPU — der GS1900 liefert sie per SNMP nicht in
  dokumentierter Form
- SNMP v3

## Entwicklung

```bash
pip install pytest-homeassistant-custom-component ruff
python -m pytest
ruff check . && ruff format --check .
```

Die Tests starten einen kleinen SNMP-Agenten auf localhost. Client,
Tabellenauswertung und Einrichtung in Home Assistant werden so durchgängig
geprüft, ohne dass ein Switch nötig ist.

## Icons

Die Icons liegen in `custom_components/zyxel_switch/brand/`. Home Assistant
liest sie dort ab **2026.3** direkt aus.

Dieses Projekt steht in keiner Verbindung zu Zyxel und wird nicht von Zyxel
unterstützt. Zyxel und das Zyxel-Logo sind Marken von Zyxel Networks und dienen
hier nur zur Kennzeichnung der unterstützten Hardware.

## Lizenz

MIT
