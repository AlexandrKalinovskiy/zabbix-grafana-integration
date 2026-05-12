# YugabyteDB — Instalacja i testy (3-node, RF=3, HA)

## Spis treści

1. [Architektura](#1-architektura)
2. [Wymagania](#2-wymagania)
3. [Uruchomienie klastra](#3-uruchomienie-klastra)
4. [Weryfikacja i testy](#4-weryfikacja-i-testy)
5. [Replikacja multi-master — testy](#5-replikacja-multi-master--testy)
6. [Dostęp do UI](#6-dostęp-do-ui)
7. [Integracja z istniejącym docker-compose](#7-integracja-z-istniejącym-docker-compose)
8. [Komendy zarządzania](#8-komendy-zarządzania)
9. [Znane problemy](#9-znane-problemy)

---

## 1. Architektura

```
Host: 192.168.40.60
│
├── yb-node1  (172.20.0.10)  ←─── seed node / primary
│   ├── YSQL   :5433   (PostgreSQL-compatible)
│   ├── YCQL   :9042   (Cassandra-compatible)
│   ├── Master UI :7000
│   ├── TServer UI :9000
│   └── YugabyteDB UI :15433
│
├── yb-node2  (172.20.0.11)  ←─── joins via --join=172.20.0.10
│   ├── YSQL   :5434   (mapowany na hoście)
│   ├── Master UI :7001
│   └── TServer UI :9001
│
└── yb-node3  (172.20.0.12)  ←─── joins via --join=172.20.0.10
    ├── YSQL   :5435   (mapowany na hoście)
    ├── Master UI :7002
    └── TServer UI :9002
```

### Jak działa multi-master w YugabyteDB

YugabyteDB to **distributed SQL** — każdy node jest jednocześnie:
- **YB-Master** — zarządza metadanymi (schemat, tablety, topologia)
- **YB-TServer** — obsługuje zapytania SQL i przechowuje dane

Dane są automatycznie shardowane na **tablety** i replikowane przez **Raft consensus**. Przy RF=3 każda partycja ma 3 repliki (jedna na każdy node). Wszystkie trzy nody mogą przyjmować **zapisy i odczyty** (active-active).

> ✅ **RF=3 (aktywne)**: Klaster **toleruje awarię 1 dowolnego noda** — quorum zachowane przy 2/3 nodach.

---

## 2. Wymagania

| Zasób | Minimum | Zalecane |
|---|---|---|
| RAM | 4 GB wolne | 8 GB |
| CPU | 2 rdzenie | 4 rdzenie |
| Dysk | 10 GB | 50 GB SSD |
| Docker | 20.10+ | 24+ |

Sprawdź dostępne zasoby:
```bash
free -h
nproc
df -h /var/lib/docker
```

---

## 3. Uruchomienie klastra

### Krok 1: Wejdź do katalogu YugabyteDB

```bash
cd /home/aleks/AntigravityProjects/zabbix-grafana-integration/yugabytedb
```

### Krok 2: Pobierz obraz (opcjonalnie — docker-compose zrobi to automatycznie)

```bash
docker pull yugabytedb/yugabyte:2025.2.2.2-b11
```

### Krok 3: Uruchom klaster

```bash
docker compose -f docker-compose.yugabyte.yml up -d
```

> Node1 startuje jako seed (ok. 60s), Node2 dołącza po healthchecku node1.
> Cały proces zajmuje ok. 2-3 minuty.

### Krok 4: Monitoruj logi

```bash
# Oba nody razem:
docker compose -f docker-compose.yugabyte.yml logs -f

# Tylko node1:
docker logs -f yb-node1

# Tylko node2:
docker logs -f yb-node2
```

---

## 4. Weryfikacja i testy

### 4.1 Status klastra

```bash
# Status node1:
docker exec yb-node1 bin/yugabyted status --base_dir=/home/yugabyte/var

# Status node2:
docker exec yb-node2 bin/yugabyted status --base_dir=/home/yugabyte/var
```

Oczekiwany output:
```
+------------------------------------------------------------------+
| yugabyted                                                        |
+------------------------------------------------------------------+
| Status              : Running.                                   |
| Replication Factor  : 2                                          |
| YugabyteDB UI       : http://172.20.0.10:15433                   |
| JDBC                : jdbc:postgresql://172.20.0.10:5433/...     |
+------------------------------------------------------------------+
```

### 4.2 Sprawdź topologię klastra (lista nodów)

```bash
docker exec yb-node1 bin/yb-admin \
  --master_addresses 172.20.0.10:7100,172.20.0.11:7100 \
  list_all_masters
```

Oczekiwany output — 2 mastery:
```
Master UUID | RPC Host | State  | Role
...         | node1    | ALIVE  | LEADER
...         | node2    | ALIVE  | FOLLOWER
```

### 4.3 Połącz się przez YSQL (PostgreSQL-compatible)

```bash
# Przez node1:
docker exec -it yb-node1 bash -c \
  '/home/yugabyte/bin/ysqlsh -h 172.20.0.10 -U yugabyte -d yugabyte'

# Przez node2 (z hosta przez port 5434):
psql -h 192.168.40.60 -p 5434 -U yugabyte -d yugabyte
# hasło: yugabyte
```

---

## 5. Replikacja multi-master — testy

### Test 1: Zapis na node1, odczyt z node2

```bash
# Terminal 1: Połącz się z node1
docker exec -it yb-node1 bash -c \
  '/home/yugabyte/bin/ysqlsh -h 172.20.0.10 -U yugabyte -d yugabyte'
```

```sql
-- Na node1: utwórz tabelę i wstaw dane
CREATE TABLE test_replication (
  id SERIAL PRIMARY KEY,
  hostname TEXT,
  message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO test_replication (hostname, message)
VALUES ('node1', 'Napisano z node1 — sprawdź na node2!');

SELECT * FROM test_replication;
```

```bash
# Terminal 2: Połącz się z node2 i sprawdź replikację
docker exec -it yb-node2 bash -c \
  '/home/yugabyte/bin/ysqlsh -h 172.20.0.11 -U yugabyte -d yugabyte'
```

```sql
-- Na node2: dane powinny być widoczne od razu
SELECT * FROM test_replication;

-- Dodaj dane z node2 (active-active!):
INSERT INTO test_replication (hostname, message)
VALUES ('node2', 'Napisano z node2 — multi-master działa!');
```

```sql
-- Wróć na node1 i sprawdź dane z node2:
SELECT * FROM test_replication ORDER BY created_at;
```

### Test 2: Symulacja awarii node2

```bash
# Zatrzymaj node2
docker stop yb-node2

# Sprawdź czy node1 nadal działa (przy RF=2 mogą być problemy z write quorum)
docker exec yb-node1 bash -c \
  '/home/yugabyte/bin/ysqlsh -h 172.20.0.10 -U yugabyte -d yugabyte -c "SELECT count(*) FROM test_replication;"'

# Przywróć node2
docker start yb-node2
```

> ⚠️ Przy RF=2 po awarii jednego noda klaster może odmówić zapisów (brak quorum Raft).
> To normalne zachowanie — dla HA w produkcji użyj RF=3.

### Test 3: Distributed transactions

```bash
docker exec -it yb-node1 bash -c \
  '/home/yugabyte/bin/ysqlsh -h 172.20.0.10 -U yugabyte -d yugabyte'
```

```sql
-- Test transakcji rozproszonych
BEGIN;
INSERT INTO test_replication (hostname, message) VALUES ('txn-test', 'transakcja 1');
INSERT INTO test_replication (hostname, message) VALUES ('txn-test', 'transakcja 2');
COMMIT;

-- Sprawdź izolację (REPEATABLE READ domyślnie w YugabyteDB):
BEGIN ISOLATION LEVEL SERIALIZABLE;
SELECT * FROM test_replication WHERE hostname = 'txn-test';
COMMIT;
```

### Test 4: pgbench — load test

```bash
# Inicjalizacja pgbench na node1
docker exec yb-node1 bash -c \
  'pgbench -h 172.20.0.10 -p 5433 -U yugabyte -i -s 10 yugabyte'

# Uruchom test obciążeniowy (60 sekund)
docker exec yb-node1 bash -c \
  'pgbench -h 172.20.0.10 -p 5433 -U yugabyte -c 10 -j 2 -T 60 yugabyte'
```

---

## 6. Dostęp do UI

| Dashboard | URL | Opis |
|---|---|---|
| **YugabyteDB UI** | `http://192.168.40.60:15433` | Główny dashboard — tablety, metryki, topologia |
| **YB-Master UI (node1)** | `http://192.168.40.60:7000` | Szczegóły mastera, tablety, uptime |
| **YB-Master UI (node2)** | `http://192.168.40.60:7001` | Master node2 |
| **YB-TServer UI (node1)** | `http://192.168.40.60:9000` | TServer metrics, tablets |
| **YB-TServer UI (node2)** | `http://192.168.40.60:9001` | TServer node2 |

---

## 7. Integracja z istniejącym docker-compose

Jeśli chcesz uruchamiać YugabyteDB razem z Zabbix/Grafana:

```bash
# Opcja A: Osobny plik (zalecane — izolacja sieci)
docker compose -f docker-compose.yml up -d          # Zabbix + Grafana
docker compose -f yugabytedb/docker-compose.yugabyte.yml up -d  # YugabyteDB

# Opcja B: Razem (wymaga dodania sieci do docker-compose.yml)
docker compose -f docker-compose.yml \
               -f yugabytedb/docker-compose.yugabyte.yml up -d
```

---

## 8. Komendy zarządzania

```bash
# Start
docker compose -f yugabytedb/docker-compose.yugabyte.yml up -d

# Stop (zachowuje dane)
docker compose -f yugabytedb/docker-compose.yugabyte.yml stop

# Restart
docker compose -f yugabytedb/docker-compose.yugabyte.yml restart

# Usuń (dane pozostają w volumes)
docker compose -f yugabytedb/docker-compose.yugabyte.yml down

# Usuń wszystko razem z danymi (UWAGA!)
docker compose -f yugabytedb/docker-compose.yugabyte.yml down -v

# Status nodów
docker exec yb-node1 bin/yugabyted status --base_dir=/home/yugabyte/var
docker exec yb-node2 bin/yugabyted status --base_dir=/home/yugabyte/var

# Lista tabletów
docker exec yb-node1 bin/yb-admin \
  --master_addresses 172.20.0.10:7100,172.20.0.11:7100 \
  list_tablets ysql.yugabyte test_replication 0

# Rebalance tabletów po zmianie topologii
docker exec yb-node1 bin/yb-admin \
  --master_addresses 172.20.0.10:7100,172.20.0.11:7100 \
  rebalance_tablets
```

---

## 9. Znane problemy

### Problem: node2 nie dołącza do klastra

```
ERROR: could not connect to master at 172.20.0.10:7100
```

**Przyczyna:** node1 jeszcze nie jest gotowy  
**Rozwiązanie:** healthcheck w docker-compose czeka na node1. Poczekaj 2-3 minuty lub sprawdź logi:
```bash
docker logs yb-node1 | tail -20
```

### Problem: "Not enough live tablet servers" 

**Przyczyna:** Klaster oczekuje RF=2 ale tylko 1 node jest up  
**Rozwiązanie:** Upewnij się że oba nody działają:
```bash
docker compose -f yugabytedb/docker-compose.yugabyte.yml ps
```

### Problem: Port 5433 zajęty przez PostgreSQL

```
Error: bind: address already in use (port 5433)
```

**Rozwiązanie:** PostgreSQL z Zabbix używa portu 5432, nie 5433 — konflikt nie powinien wystąpić. Jeśli jednak jest problem:
```bash
ss -tlnp | grep 5433
```

### Problem: Brak pamięci RAM

YugabyteDB wymaga minimum 2 GB RAM na node. Sprawdź:
```bash
docker stats yb-node1 yb-node2
```

---

*Dokumentacja dla YugabyteDB 2025.2.2.2 na serwerze 192.168.40.60 (Debian 13)*
