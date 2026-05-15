# Pangolin HA — Architektura i dokumentacja

## Spis treści

1. [Architektura](#1-architektura)
2. [Uruchomienie](#2-uruchomienie)
3. [Pliki i struktura](#3-pliki-i-struktura)
4. [Porty i dostęp](#4-porty-i-dostęp)
5. [Health checking](#5-health-checking)
6. [Sticky sessions](#6-sticky-sessions)
7. [Failover — jak testować](#7-failover--jak-testować)
8. [Znane zachowania i gotchas](#8-znane-zachowania-i-gotchas)

---

## 1. Architektura

```
Przeglądarka użytkownika
         │
         ▼
  HAProxy :80 (sticky SERVERID cookie)
  ┌───────┴────────┐
  │                │
  ▼                ▼
pangolin-1      pangolin-2       ← Next.js UI :3002 + API :3000
  │                │
  ▼                ▼
yb-node1        yb-node2         ← YugabyteDB YSQL :5433 (wewnętrzne)
  └──────┬─────────┘
         │
       yb-node3                  ← YugabyteDB node3 (RF=3 quorum)

Health check sidecary:
  hc-node1 :9000 → sprawdza pangolin-1 API + yb-node1 TCP
  hc-node2 :9000 → sprawdza pangolin-2 API + yb-node2 TCP

Fallback:
  sorry-page (nginx) → gdy OBA pangolinki DOWN → strona maintenance
```

### Zasady działania

- **HAProxy** przyjmuje ruch na porcie 80 i rozdziela na dwa Pangolinki
- **Routing przez HAProxy**: `/api/v1/*` → port 3000 (API), reszta → port 3002 (UI)
- **Health check**: HAProxy nie pyta Pangolina bezpośrednio — pyta sidecar który sprawdza OBE usługi
- **Failover**: gdy sidecar zwróci HTTP 503 → serwer oznaczony DOWN w ~3s (`fall 1`)
- **Sticky session**: ciastko `SERVERID=p1|p2` — przeglądarka przyklejona do jednego Pangolina
- **Gdy serwer DOWN**: `option redispatch` — nowy serwer, nowe ciastko, automatycznie

---

## 2. Uruchomienie

```bash
cd pangolin/

# Zbuduj sidecar obrazy i uruchom cały stack
docker compose -f docker-compose.ha.yml up -d --build

# Sprawdź status
docker compose -f docker-compose.ha.yml ps

# Logi wszystkich serwisów
docker compose -f docker-compose.ha.yml logs -f

# Logi konkretnego serwisu
docker compose -f docker-compose.ha.yml logs -f pangolin-1
docker compose -f docker-compose.ha.yml logs -f haproxy
```

### Przeładowanie HAProxy bez downtime

```bash
docker exec haproxy-pangolin haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg  # walidacja
docker kill -s HUP haproxy-pangolin                                              # graceful reload
```

---

## 3. Pliki i struktura

```
pangolin/
├── docker-compose.ha.yml       # Główny stack HA (2×Pangolin + sidecary + HAProxy + sorry)
├── docker-compose.pangolin.yml # Pojedyncza instancja (archiwum — nie używać)
├── haproxy.cfg                 # Konfiguracja HAProxy (routing, health, sticky)
├── healthcheck.py              # Kod sidecar — sprawdza Pangolin HTTP + YugabyteDB TCP
├── Dockerfile.healthcheck      # Obraz dla sidecarów hc-node1/hc-node2
├── maintenance.html            # Strona wyświetlana podczas failovera
├── config-node1/
│   └── config.yml              # Pangolin-1 → yb-node1 (172.20.0.10:5433)
├── config-node2/
│   └── config.yml              # Pangolin-2 → yb-node2 (172.20.0.11:5433)
└── config/
    └── config.yml              # (archiwum single-instance)
```

---

## 4. Porty i dostęp

| Adres | Co |
|---|---|
| `http://192.168.40.60` | Pangolin UI przez HAProxy |
| `http://192.168.40.60:8404/stats` | HAProxy dashboard (UP/DOWN live, 2s refresh) |
| `http://192.168.40.60:15433` | YugabyteDB UI (node1) |
| `http://192.168.40.60:15434` | YugabyteDB UI (node2, backup) |
| `http://192.168.40.60:15435` | YugabyteDB UI (node3, backup) |

### Porty wewnętrzne (Docker network)

| Serwis | Port wewnętrzny | Opis |
|---|---|---|
| pangolin-1/2 | 3000 | API Pangolina |
| pangolin-1/2 | 3001 | Internal API (health check) |
| pangolin-1/2 | 3002 | Next.js UI |
| hc-node1/2 | 9000 | Sidecar health check endpoint |
| yb-node1 | 5433 | YugabyteDB YSQL (wewnętrzne) |
| yb-node2 | 5433 | YugabyteDB YSQL (wewnętrzne) |
| yb-node3 | 5433 | YugabyteDB YSQL (wewnętrzne) |

---

## 5. Health checking

HAProxy używa **dwupoziomowego health checku** przez dedykowane sidecar kontenery:

```
HAProxy → GET http://hc-node1:9000/
              │
              ├─ curl pangolin-1:3001/api/v1/   (Pangolin żyje?)
              └─ socket.connect(172.20.0.10, 5433)  (yb-node1 żyje?)

Wynik: 200 OK = oba zdrowe
       503     = cokolwiek padło → HAProxy: serwer DOWN
```

**Parametry failovera:**

| Parametr | Wartość | Znaczenie |
|---|---|---|
| `inter 3s` | co 3s | częstotliwość sprawdzania |
| `fall 1` | 1 nieudany | czas do oznaczenia DOWN: ~3s |
| `rise 2` | 2 udane | czas do powrotu: ~2s |
| `fastinter 1s` | co 1s | sprawdzanie gdy serwer DOWN |

---

## 6. Sticky sessions

HAProxy wstrzykuje ciastko `SERVERID` przy pierwszym requescie:

```
Set-Cookie: SERVERID=p1; path=/    ← przeglądarka przyklejona do pangolin-1
Set-Cookie: SERVERID=p2; path=/    ← przeglądarka przyklejona do pangolin-2
```

- `indirect` — ciastko usuwane zanim dotrze do Pangolina
- `nocache` — zapobiega cachowaniu przez proxy
- `maxlife 3600` — wygasa po 1h
- Gdy serwer DOWN: `redispatch` przenosi na drugi i aktualizuje ciastko

---

## 7. Failover — jak testować

```bash
# Zatrzymaj YugabyteDB node1 → pangolin-1 wypadnie z rotacji
docker stop yb-node1

# Obserwuj w czasie rzeczywistym:
watch -n1 'curl -s "http://192.168.40.60:8404/stats;csv" | grep "pangolin_" | cut -d, -f2,18,19'

# Przywróć
docker start yb-node1

# Zatrzymaj samego Pangolina
docker stop pangolin-1

# Sprawdź czy sorry page się pojawia (gdy OBA down):
docker stop pangolin-1 pangolin-2
curl -s http://192.168.40.60/ | grep "<title>"
docker start pangolin-1 pangolin-2
```

---

## 8. Znane zachowania i gotchas

### YugabyteDB port wewnętrzny vs. zewnętrzny

```
Z hosta:
  yb-node1 → 192.168.40.60:5433
  yb-node2 → 192.168.40.60:5434  ← :5434, nie :5433 !
  yb-node3 → 192.168.40.60:5435

Wewnątrz Docker network:
  yb-node1 → 172.20.0.10:5433
  yb-node2 → 172.20.0.11:5433   ← zawsze :5433 !
  yb-node3 → 172.20.0.12:5433   ← zawsze :5433 !
```

### Pangolin API routing

Bez Traefika, HAProxy sam robi path-based routing:
- `/api/v1/*` → pangolin:3000 (API backend)
- wszystko inne → pangolin:3002 (Next.js UI)

Kierowanie wszystkiego na port 3002 powoduje **404 z Next.js** dla zapytań API.

### `domains.verified` w bazie

Przy konfiguracji z IP (`base_domain: "192.168.40.60"`) domena nie przejdzie
automatycznej weryfikacji DNS. Trzeba ustawić ręcznie:

```sql
UPDATE domains SET verified = true, failed = false WHERE "domainId" = 'domain1';
```

### YugabyteDB quorum

- RF=3, 3 nody → klaster toleruje utratę **dokładnie 1 nody**
- Przy 2 zatrzymanych nodach → utrata quorum → cały klaster przestaje obsługiwać SQL
- Po zatrzymaniu nody może minąć ~10-30s zanim klaster przeprowadzi leader election

### pg-connection-string nie obsługuje multi-host URI

Node.js `pg` library (używana przez Pangolin/Drizzle) nie parsuje:
```
postgresql://user@host1:5433,host2:5433,host3:5433/db  ← NIE DZIAŁA
```
Zamiast tego: każdy Pangolin łączy się z "lokalnym" nodem YugabyteDB,
a HAProxy/sidecar obsługuje wykrywanie awarii.

---

*Dokumentacja wygenerowana: 2026-05-12*
