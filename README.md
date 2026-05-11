# Zabbix + Grafana Integration — Dokumentacja

## Spis treści

1. [Przegląd architektury](#1-przegląd-architektury)
2. [Wersje oprogramowania](#2-wersje-oprogramowania)
3. [Struktura projektu](#3-struktura-projektu)
4. [Docker Compose — szczegółowy opis](#4-docker-compose--szczegółowy-opis)
5. [Sieć Docker i komunikacja między kontenerami](#5-sieć-docker-i-komunikacja-między-kontenerami)
6. [Zabbix — konfiguracja monitorowania](#6-zabbix--konfiguracja-monitorowania)
7. [Grafana — plugin i datasource](#7-grafana--plugin-i-datasource)
8. [Provisioning Grafany](#8-provisioning-grafany)
9. [Dashboardy](#9-dashboardy)
10. [Port Monitor — wizualizacja portów](#10-port-monitor--wizualizacja-portów)
11. [Format zapytań do pluginu Zabbix](#11-format-zapytań-do-pluginu-zabbix)
12. [Dane dostępowe](#12-dane-dostępowe)
13. [Uruchamianie i zarządzanie](#13-uruchamianie-i-zarządzanie)
14. [Znane problemy i rozwiązania](#14-znane-problemy-i-rozwiązania)

---

## 1. Przegląd architektury

```
┌─────────────────────────────────────────────────────────────┐
│                     Host: 192.168.40.60                     │
│                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ Grafana  │───▶│ Zabbix Web   │───▶│  PostgreSQL  │       │
│  │ :3000    │    │ (Nginx):8080 │    │  :5432       │       │
│  └──────────┘    └──────────────┘    └──────────────┘       │
│       │                 │                    ▲              │
│       │          ┌──────────────┐            │              │
│       │          │ Zabbix Server│────────────┘              │
│       │          │ :10051       │                           │
│       │          └──────────────┘                           │
│       │                                                     │
│  ┌──────────────┐   SVG /asr.svg, /juniper.svg              │
│  │ Port Monitor │◀──────────────────────────── Grafana      │
│  │ :8090        │──── Zabbix API (JSON-RPC) ──▶ Zabbix Web  │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
        │                 │ SNMP (UDP 161)
        │                 ▼
        │    ┌────────────────────────────┐
        │    │    Urządzenia sieciowe     │
        │    │                           │
        │    │  Nexus  172.16.2.11  SNMPv2│
        │    │  Nexus  172.16.2.101 SNMPv2│
        │    │  ASR    172.16.2.200 SNMPv3│
        │    │  Juniper172.16.2.105 SNMPv3│
        │    └────────────────────────────┘
        │
        │  HTTP API (JSON-RPC)
        └──────────────────────▶ Zabbix Web :8080
```

**Przepływ danych:**
1. Zabbix Server odpytuje urządzenia przez SNMP → zapisuje dane w PostgreSQL
2. Grafana przez plugin odpytuje Zabbix API (JSON-RPC) → pobiera dane historyczne
3. Port Monitor odpytuje Zabbix API → generuje SVG z mapą portów
4. Grafana osadza SVG (`<img>`) bezpośrednio w panelach dashboardu

---

## 2. Wersje oprogramowania

| Komponent | Wersja | Docker Image |
|---|---|---|
| **Zabbix Server** | 7.0-alpine | `zabbix/zabbix-server-pgsql:7.0-alpine-latest` |
| **Zabbix Web** | 7.0-alpine | `zabbix/zabbix-web-nginx-pgsql:7.0-alpine-latest` |
| **Zabbix Agent 2** | 7.0-alpine | `zabbix/zabbix-agent2:7.0-alpine-latest` |
| **PostgreSQL** | 16-alpine | `postgres:16-alpine` |
| **Grafana** | latest | `grafana/grafana:latest` |
| **Plugin Zabbix** | 6.3.2 | `alexanderzobnin-zabbix-app` |
| **Docker** | 29.4.3 | — |
| **Host OS** | Debian 13 | Kernel 6.12.x |

---

## 3. Struktura projektu

```
zabbix-grafana-integration/
├── docker-compose.yml                          # Definicja wszystkich serwisów
├── install-docker.sh                           # Skrypt instalacji Dockera
├── README.md                                   # Ta dokumentacja
├── port-monitor/                               # Mikroserwis wizualizacji portów
│   ├── Dockerfile                              # obraz python:3.12-alpine
│   └── server.py                               # serwer HTTP + logika SVG
└── grafana/
    └── provisioning/                           # Auto-konfiguracja Grafany
        ├── datasources/
        │   └── zabbix.yml                      # Definicja źródła danych Zabbix
        └── dashboards/
            ├── dashboards.yml                  # Konfiguracja dostawcy dashboardów
            ├── network-switch-overview.json    # Dashboard: przełączniki Nexus
            └── core-network-traffic.json       # Dashboard: ASR i Juniper
```

---

## 4. Docker Compose — szczegółowy opis

Plik `docker-compose.yml` definiuje 5 serwisów połączonych wewnętrzną siecią `zabbix-net`.

### 4.1 PostgreSQL

```yaml
postgres:
  image: postgres:16-alpine
  container_name: zabbix-postgres
  environment:
    POSTGRES_DB: zabbix
    POSTGRES_USER: zabbix
    POSTGRES_PASSWORD: zabbix_pass
  volumes:
    - postgres-data:/var/lib/postgresql/data   # persystentne dane
  healthcheck:
    test: pg_isready -U zabbix -d zabbix
    interval: 10s
```

- Baza danych dla całego Zabbixa (hosty, itemy, historia, trendy)
- Volumen `postgres-data` zachowuje dane po restarcie kontenera
- Healthcheck zapewnia że Zabbix Server startuje dopiero po gotowości bazy

### 4.2 Zabbix Server

```yaml
zabbix-server:
  image: zabbix/zabbix-server-pgsql:7.0-alpine-latest
  ports:
    - "10051:10051"     # port dla agentów Zabbix
  environment:
    DB_SERVER_HOST: postgres
    ZBX_ENABLE_SNMP_TRAPS: "true"
  depends_on:
    postgres:
      condition: service_healthy
```

- Serce systemu — przetwarza dane z agentów i SNMP
- Port 10051 jest potrzebny do komunikacji z Zabbix Agent
- `depends_on` z `condition: service_healthy` gwarantuje kolejność startu

### 4.3 Zabbix Web (Nginx)

```yaml
zabbix-web:
  image: zabbix/zabbix-web-nginx-pgsql:7.0-alpine-latest
  ports:
    - "8080:8080"
  environment:
    ZBX_SERVER_HOST: zabbix-server
    PHP_TZ: Europe/Warsaw
```

- Frontend PHP/Nginx dla Zabbix UI oraz **JSON-RPC API**
- API dostępne pod `http://192.168.40.60:8080/api_jsonrpc.php`
- Grafana komunikuje się z Zabbixem **wyłącznie przez to API**

### 4.4 Zabbix Agent 2

```yaml
zabbix-agent:
  image: zabbix/zabbix-agent2:7.0-alpine-latest
  environment:
    ZBX_HOSTNAME: "Zabbix server"
    ZBX_SERVER_HOST: zabbix-server
```

- Monitoruje sam serwer Zabbix (CPU, RAM, procesy)
- Nie wystawia portów na zewnątrz — komunikacja wewnątrz sieci Docker

### 4.5 Grafana

```yaml
grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"
  environment:
    GF_SECURITY_ADMIN_USER: admin
    GF_SECURITY_ADMIN_PASSWORD: "Op2oyxq##"
    GF_INSTALL_PLUGINS: alexanderzobnin-zabbix-app
    GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS: alexanderzobnin-zabbix-app
  volumes:
    - grafana-data:/var/lib/grafana
    - ./grafana/provisioning:/etc/grafana/provisioning
```

- `GF_INSTALL_PLUGINS` — automatyczna instalacja pluginu przy starcie
- `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS` — wymagane bo plugin nie ma oficjalnego certyfikatu Grafana
- Volumen `./grafana/provisioning` montuje lokalny katalog do kontenera

---

## 5. Sieć Docker i komunikacja między kontenerami

Wszystkie kontenery są w sieci `zabbix-net` (bridge). Dzięki temu:

| Połączenie | Adres w sieci Docker |
|---|---|
| Grafana → Zabbix API | `http://zabbix-web:8080/api_jsonrpc.php` |
| Zabbix Server → PostgreSQL | `postgres:5432` |
| Zabbix Web → PostgreSQL | `postgres:5432` |
| Zabbix Web → Zabbix Server | `zabbix-server:10051` |
| Zabbix Agent → Zabbix Server | `zabbix-server:10051` |

> **Ważne:** W datasource Grafany URL wskazuje na `zabbix-web` (nazwę kontenera), a NIE na `localhost`. To kluczowe — kontenery komunikują się przez wewnętrzne DNS sieci Docker.

---

## 6. Zabbix — konfiguracja monitorowania

### 6.1 Monitorowane urządzenia

| Host | IP | Protokół | Wersja SNMP | Uwagi |
|---|---|---|---|---|
| core-edge-nexus-k1-sw1 | 172.16.2.11 | SNMP | v2c | Community: `{$SNMP_COMMUNITY}` |
| CORE-SG-K1-SW1 | 172.16.2.101 | SNMP | v2c | Community: `{$SNMP_COMMUNITY}` |
| CISCO ASR | 172.16.2.200 | SNMP | v3 | authPriv, SHA+AES |
| JUNIPER | 172.16.2.105 | SNMP | v3 | authPriv |

### 6.2 Użyte szablony Zabbix

| Urządzenie | Szablon Zabbix |
|---|---|
| Cisco Nexus 9000 | `Cisco Nexus 9000 Series by SNMP` |
| Cisco ASR 1000 | `Cisco IOS by SNMP` |
| Juniper MX204 | `Juniper Junos by SNMP` |

### 6.3 Kluczowe zbierane metryki (SNMP)

| Metryka | OID / Item name | Jednostka |
|---|---|---|
| CPU utilization | `#22: CPU utilization` | % |
| Bits received | `Interface X: Bits received` | bps |
| Bits sent | `Interface X: Bits sent` | bps |
| Interface speed | `Interface X: Speed` | bps |
| Operational status | `Interface X: Operational status` | 1=UP, 2=DOWN |
| Inbound errors | `Interface X: Inbound packets with errors` | pps |
| Outbound errors | `Interface X: Outbound packets with errors` | pps |
| Inbound discarded | `Interface X: Inbound packets discarded` | pps |
| Transceiver temp | `Transceiver Temperature Sensor: Temperature` | °C |

### 6.4 Makra globalne

| Makro | Wartość | Użycie |
|---|---|---|
| `{$SNMP_COMMUNITY}` | (skonfigurowane w Zabbix UI) | community string dla SNMPv2c |

### 6.5 SNMPv3 — konfiguracja

Dla urządzeń z SNMPv3 (ASR, Juniper) w Zabbix konfiguruje się per-host:
- **Security level:** `authPriv`
- **Auth protocol:** SHA
- **Priv protocol:** AES128
- Dane wpisywane bezpośrednio w interfejsie hosta (nie przez makra)

---

## 7. Grafana — plugin i datasource

### 7.1 Plugin alexanderzobnin-zabbix-app

Plugin to **backend plugin** Grafany (ma komponent Go):
- Część **frontend** (JavaScript/React) — UI paneli w przeglądarce
- Część **backend** (Go binary) — wykonuje zapytania do Zabbix API po stronie serwera

Plugin instaluje się automatycznie przez zmienną środowiskową:
```
GF_INSTALL_PLUGINS: alexanderzobnin-zabbix-app
```

### 7.2 Datasource — plik `grafana/provisioning/datasources/zabbix.yml`

```yaml
datasources:
  - name: Zabbix
    type: alexanderzobnin-zabbix-datasource
    access: proxy
    url: http://zabbix-web:8080/api_jsonrpc.php
    jsonData:
      username: Admin
      trends: true        # włącz dane trendów (zagregowane hourly)
      trendsFrom: "7d"    # dane starsze niż 7 dni ładuj z trendów
      trendsRange: "4d"   # zakres przy którym przełącz na trendy
      cacheTTL: "1h"      # czas cache wyników w pluginie
    secureJsonData:
      password: "Op2oyxq##"
    isDefault: true
    editable: true
```

**Kluczowe parametry:**
- `url` — wskazuje na wewnętrzny adres Zabbix Web (`zabbix-web:8080`)
- `trends: true` — dla zakresów > `trendsFrom` plugin automatycznie używa danych trendów (szybciej, mniej danych)
- `cacheTTL` — plugin cache'uje listy hostów/itemów po stronie backendu

Po załadowaniu datasource można sprawdzić jego UID:
```bash
curl -u admin:hasło http://localhost:3000/api/datasources
# uid: PA67C5EADE9207728
```

---

## 8. Provisioning Grafany

Grafana obsługuje **automatyczne ładowanie** datasource'ów i dashboardów z plików przy starcie — bez potrzeby ręcznej konfiguracji przez UI.

### 8.1 Konfiguracja dostawcy dashboardów — `dashboards.yml`

```yaml
providers:
  - name: default
    orgId: 1
    folder: "Zabbix"          # folder w Grafana UI
    folderUid: "zabbix"
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30  # co 30s sprawdza zmiany plików
    allowUiUpdates: true       # pozwala edytować przez UI (i zapisuje z powrotem)
    options:
      path: /etc/grafana/provisioning/dashboards
```

- `updateIntervalSeconds: 30` — zmiany w plikach JSON są automatycznie ładowane co 30 sekund
- `allowUiUpdates: true` — edycje w Grafana UI są możliwe (choć nie zapisywane z powrotem do pliku)

### 8.2 Montowanie przez Docker volume

```yaml
volumes:
  - ./grafana/provisioning:/etc/grafana/provisioning
```

Lokalny katalog `./grafana/provisioning` jest montowany bezpośrednio do kontenera. Edycja plików na hoście → zmiany widoczne w kontenerze natychmiast.

---

## 9. Dashboardy

### 9.1 Network Switch Overview (`network-switch-overview.json`)

**UID:** `network-switch-overview`  
**Host:** `core-edge-nexus-k1-sw1` (172.16.2.11)

Zawiera panele:
- CPU Utilization (time series)
- Temperatury transceiverów (time series + stat)
- Ruch przychodzący / wychodzący (time series)
- Status interfejsów UP/DOWN (stat z mappingiem kolorów)
- Błędy i odrzucone pakiety (bar chart z progiem alarmowym)

### 9.2 Core Network Traffic (`core-network-traffic.json`)

**UID:** `core-network-traffic`  
**Układ:** Dwukolumnowy — ASR po lewej (x=0–11), Juniper po prawej (x=12–23)

Każdy interfejs zajmuje jeden rząd (h=8 dla ASR, h=12 dla Juniper):

```
[⚡ Speed  ] [─────── Traffic In+Out (8w) ────────] [❌ Err]
[🔌 Status ]
```

**Interfejsy ASR (lewa kolumna):**
- `Te0/0/0` — EPIX-WAW-TRUNK (~900 Mbps)
- `Gi0/0/4` — ZdalnyAdmin BGP R2
- `Gi0/0/5` — DC WAN 85.202.58.0/24

**Interfejsy Juniper (prawa kolumna):**
- `et-0/0/0` — EPIX-KAT trunk fizyczny (~600 Mbps)
- `et-0/0/1` — E-Systemy / DC WAN

**Timepicker:** zakresy 1h, 2h, 3h, 6h, 12h, 24h, 2d, 7d, 14d, 30d  
**Auto-refresh:** 1 minuta

---

## 10. Port Monitor — wizualizacja portów

Dedykowany mikroserwis generujący interaktywną mapę portów routerów **bezpośrednio w dashboardzie Grafany**.

### 10.1 Architektura

```
port-monitor (kontener :8090)
    │
    ├── GET /asr.svg      → SVG: mapa portów Cisco ASR 1000
    ├── GET /juniper.svg  → SVG: mapa portów Juniper MX204
    ├── GET /asr          → HTML: pełna strona z animacjami (standalone)
    ├── GET /juniper      → HTML: pełna strona z animacjami (standalone)
    ├── GET /api/data     → JSON: surowe dane portów
    └── GET /             → HTML: pełny dashboard obu routerów
```

### 10.2 Jak działa

1. Port Monitor odpytuje **Zabbix API** (JSON-RPC) o itemy:
   - `Interface.*: Operational status` — UP/DOWN
   - `Interface.*: Speed` — prędkość (1G/10G/40G/100G)
   - `Interface.*: Inbound/Outbound packets with errors` — błędy

2. Każdy port dostaje status:
   - `up` 🟢 — prędkość > 0 i op_status = 1
   - `down` 🔴 — op_status = 2
   - `errors` 🟠 — są błędy pakietów
   - `disabled` ⚫ — brak danych (speed=0 i op_status=0)

3. Generator SVG tworzy obraz wektorowy z portami pogrupowanymi wg prędkości.

### 10.3 Integracja z Grafaną (SVG jako `<img>`)

Grafana osadza wizualizację przez **Text panel** (HTML mode) z tagiem `<img>`:

```html
<img src="http://192.168.40.60:8090/asr.svg"
     width="100%" height="auto"
     style="display:block;max-width:100%;">
```

> **Dlaczego SVG a nie iframe?**  
> Grafana 13 używa `display:flex` dla paneli. `width:100%` na `<iframe>` rozwiązuje się do naturalnego rozmiaru elementu (~300px), nie szerokości panelu. Tag `<img>` z `width="100%"` skaluje się poprawnie w każdym kontekście CSS.

### 10.4 Konfiguracja serwisu w docker-compose

```yaml
port-monitor:
  build: ./port-monitor
  container_name: port-monitor
  restart: unless-stopped
  ports:
    - "8090:8090"
  environment:
    ZABBIX_URL:  http://zabbix-web:8080/api_jsonrpc.php
    ZABBIX_USER: Admin
    ZABBIX_PASS: Op2oyxq##
  networks:
    - zabbix-net
```

### 10.5 Zmienne środowiskowe port-monitor

| Zmienna | Wartość | Opis |
|---|---|---|
| `ZABBIX_URL` | `http://zabbix-web:8080/api_jsonrpc.php` | Adres Zabbix API |
| `ZABBIX_USER` | `Admin` | Użytkownik Zabbix |
| `ZABBIX_PASS` | `Op2oyxq##` | Hasło Zabbix |
| `PORT` | `8090` (domyślny) | Port HTTP serwera |

### 10.6 Konwencja nazewnictwa portów w Zabbix

Port Monitor rozpoznaje porty przez regex pasujące do nazw itemów Zabbix:

| Urządzenie | Prefiks itemu | Przykład |
|---|---|---|
| Cisco ASR | `Interface Te0/0/X` | `Interface Te0/0/0(EPIX-WAW-TRUNK): Bits received` |
| Cisco ASR | `Interface Gi0/0/X` | `Interface Gi0/0/4(ZdalnyAdmin): Operational status` |
| Juniper MX | `Interface et-0/0/X` | `Interface et-0/0/0][]: Bits received` |

### 10.7 Rebuild i restart

```bash
# Po zmianie server.py:
docker compose build port-monitor
docker compose up -d port-monitor

# Weryfikacja SVG:
curl -s http://localhost:8090/asr.svg | head -c 200

# Pełna strona (z animacjami hover):
# http://192.168.40.60:8090/
```

---

## 11. Format zapytań do pluginu Zabbix

To najważniejsza techniczna sekcja — format zapytań był przyczyną większości problemów debugowania.

### 10.1 Poprawny format target (query) w panelu

```json
{
  "refId": "A",
  "datasource": {
    "type": "alexanderzobnin-zabbix-datasource",
    "uid": "PA67C5EADE9207728"
  },
  "mode": 0,
  "queryType": "0",
  "schema": 12,
  "group":       { "filter": "/.*/"},
  "host":        { "filter": "CISCO ASR" },
  "application": { "filter": "" },
  "item":        { "filter": "/EPIX-WAW-TRUNK.*Bits/" },
  "itemTag":     { "filter": "" },
  "macro":       { "filter": "" },
  "proxy":       { "filter": "" },
  "tags":        { "filter": "" },
  "functions": [],
  "resultFormat": "time_series",
  "countTriggers": true,
  "countTriggersBy": "",
  "evaltype": "0",
  "minSeverity": 3,
  "table": { "skipEmptyValues": false },
  "options": {
    "count": false,
    "disableDataAlignment": false,
    "showDisabledItems": false,
    "skipEmptyValues": true,
    "useTrends": "default",
    "useZabbixValueMapping": false
  }
}
```

### 10.2 Kluczowe pola i ich znaczenie

| Pole | Wartość | Znaczenie |
|---|---|---|
| `mode` | `0` (integer) | Typ zapytania: 0=Metrics. Backend Go sprawdza to pole. |
| `queryType` | `"0"` (string) | Duplikat mode dla frontendu JS. Musi być stringiem. |
| `schema` | `12` | Wersja schematu zapytania. Wymagane przez Go backend. |
| `group.filter` | `"/.*/"`| Regex — musi być niepusty. Pusty string = 0 wyników! |
| `item.filter` | `"/PATTERN/"` | Regex z ukośnikami. Literały nie działają! |

### 10.3 Filtry itemów — zasady

**❌ Nie działa (literal string):**
```json
"item": { "filter": "EPIX-WAW-TRUNK" }
```

**✅ Działa (regex):**
```json
"item": { "filter": "/EPIX-WAW-TRUNK/" }
```

Filtr jest traktowany jako regex tylko jeśli zaczyna się i kończy na `/`.

**Przykłady filtrów:**

| Co filtrować | Filtr |
|---|---|
| Konkretna nazwa interfejsu | `/EPIX-WAW-TRUNK/` |
| Nazwa z kropką (regex escape) | `/WAW-POL\.Mix/` |
| Tylko Bits (ruch) z interfejsu | `/EPIX-WAW-TRUNK.*Bits/` |
| Tylko status operacyjny | `/EPIX-WAW-TRUNK.*Operational status/` |
| Interfejs fizyczny Juniper et-0/0/0 | `/et-0\/0\/0\]\[/` |
| CPU utilization | `/CPU utilization/` |

### 10.4 Dlaczego group.filter nie może być pusty

Backend Go pluginu wykonuje zapytanie:
1. Pobiera listę grup matching `group.filter`
2. Pobiera hosty z tych grup matching `host.filter`
3. Pobiera itemy matching `item.filter`

Gdy `group.filter = ""` — backend nie zwraca żadnych grup → 0 hostów → 0 itemów → 0 frames.  
Rozwiązanie: `group.filter = "/.*/"`  (dopasuj wszystkie grupy).

### 10.5 Testowanie zapytań przez API

```bash
curl -u admin:hasło -X POST http://localhost:3000/api/ds/query \
  -H "Content-Type: application/json" \
  -d '{
    "queries": [{ ...target JSON... }],
    "from": "now-3h",
    "to": "now"
  }'
```

Odpowiedź:
```json
{
  "results": {
    "A": {
      "frames": [
        {
          "schema": { "name": "CISCO ASR: Interface Te0/0/0(EPIX-WAW-TRUNK): Bits received" },
          "data": { "values": [[timestamps...], [values...]] }
        }
      ]
    }
  }
}
```

---

## 11. Dane dostępowe

| Serwis | URL | Login | Hasło |
|---|---|---|---|
| Grafana | `http://192.168.40.60:3000` | `admin` | `Op2oyxq##` |
| Zabbix | `http://192.168.40.60:8080` | `Admin` | `Op2oyxq##` |

### Datasource Grafana

| Pole | Wartość |
|---|---|
| Name | `Zabbix` |
| UID | `PA67C5EADE9207728` |
| Type | `alexanderzobnin-zabbix-datasource` |
| URL | `http://zabbix-web:8080/api_jsonrpc.php` |

### Linki do dashboardów (kiosk mode)

```
# Normalny widok
http://192.168.40.60:3000/d/network-switch-overview
http://192.168.40.60:3000/d/core-network-traffic

# Fullscreen (bez navbar)
http://192.168.40.60:3000/d/core-network-traffic?kiosk
http://192.168.40.60:3000/d/core-network-traffic?kiosk&refresh=1m
```

---

## 12. Uruchamianie i zarządzanie

### Start całego stacku

```bash
cd /home/aleks/AntigravityProjects/zabbix-grafana-integration
docker compose up -d
```

### Restart pojedynczego serwisu

```bash
docker compose restart grafana        # po zmianach dashboardów
docker compose restart zabbix-server  # po zmianach konfiguracji
```

### Sprawdzenie statusu

```bash
docker compose ps
docker compose logs -f grafana        # logi Grafany na żywo
docker compose logs -f zabbix-server  # logi Zabbix Servera
```

### Zatrzymanie

```bash
docker compose down           # zatrzymaj, zachowaj dane
docker compose down -v        # zatrzymaj i usuń wolumeny (UWAGA: usuwa dane!)
```

### Aktualizacja dashboardów

Edytuj pliki JSON w `grafana/provisioning/dashboards/` — Grafana ładuje je automatycznie co 30 sekund (bez restartu).

Jeśli zmiany nie są widoczne:
```bash
docker compose restart grafana
```

### Dodanie nowego dashboardu

1. Utwórz plik `grafana/provisioning/dashboards/nazwa.json`
2. Ustaw unikalne `"uid": "moj-dashboard"` w JSON
3. Poczekaj 30s lub zrestartuj Grafanę

---

## 13. Znane problemy i rozwiązania

### Problem: "No data" w panelach

**Przyczyna 1:** Filtr `item.filter` jest literałem, nie regexem  
**Rozwiązanie:** Owiń w ukośniki: `"NAZWA"` → `"/NAZWA/"`

**Przyczyna 2:** Filtr `group.filter` jest pustym stringiem  
**Rozwiązanie:** Ustaw na `"/.*/"`

**Przyczyna 3:** Brakujące pola `schema: 12`, `queryType: "0"`, lub `mode: 0`  
**Rozwiązanie:** Użyj pełnego formatu targetu jak w sekcji 10.1

### Problem: "Non-metrics queries are not supported"

**Przyczyna:** Backend Go nie widzi pola `mode` lub ma niepoprawny typ  
**Rozwiązanie:** Ustaw `"mode": 0` (integer, nie string!)

### Problem: SNMP timeout na urządzeniu

**Możliwe przyczyny:**
- Zła community string (SNMPv2c)
- ACL na urządzeniu blokuje IP serwera (192.168.40.60)
- SNMP skonfigurowane w innym VRF niż interfejs zarządzający
- Urządzenie wymaga SNMPv3 (z hasłem)

**Diagnostyka:**
```bash
# Test z hosta
snmpget -v2c -c public -On 172.16.2.X .1.3.6.1.2.1.1.1.0

# Test SNMPv3
snmpget -v3 -l authPriv -u USER -a SHA -A AUTHPASS \
        -x AES -X PRIVPASS 172.16.2.X .1.3.6.1.2.1.1.1.0
```

### Problem: Dashboard nie ładuje się po edycji JSON

**Przyczyna:** Błąd składni JSON  
**Sprawdzenie:**
```bash
python3 -m json.tool grafana/provisioning/dashboards/nazwa.json
```

### Problem: Grafana nie łączy się z Zabbix (datasource health fail)

**Sprawdzenie:**
```bash
# Czy kontenery w tej samej sieci?
docker network inspect zabbix-grafana-integration_zabbix-net

# Czy Zabbix Web odpowiada?
docker exec grafana curl -s http://zabbix-web:8080/ping
```

---

*Dokumentacja aktualna na dzień 2026-05-08. Projekt uruchomiony na serwerze 192.168.40.60.*
