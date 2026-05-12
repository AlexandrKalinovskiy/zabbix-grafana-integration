#!/usr/bin/env python3
# Użycie: python3 test_int_pk.py [--no-cleanup]
"""
YugabyteDB — Test tabeli z BIGSERIAL (integer PK) w klastrze multi-master.

Cel: pokazać jak YugabyteDB obsługuje sekwencje/auto-increment
w środowisku distributed — oba nody piszą, IDs nie kolidują,
ale mogą być nieciągłe (luki w sekwencji).

Użycie:
    python3 test_int_pk.py
"""

import argparse, hashlib, random, string, time, sys
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Zainstaluj: sudo apt-get install python3-psycopg2"); sys.exit(1)

NODE1 = dict(host="192.168.40.60", port=5433, database="yugabyte",
             user="yugabyte", password="yugabyte", connect_timeout=5)
NODE2 = dict(host="192.168.40.60", port=5434, database="yugabyte",
             user="yugabyte", password="yugabyte", connect_timeout=5)

TABLE = "replication_test_int"
GRN, RED, YEL, CYN, BLD, RST = "\033[92m","\033[91m","\033[93m","\033[96m","\033[1m","\033[0m"

def rand_payload(n=30):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))

def checksum(s):
    return hashlib.sha256(s.encode()).hexdigest()[:12]

def connect(cfg, label):
    conn = psycopg2.connect(**cfg)
    conn.autocommit = True
    print(f"  {GRN}✓{RST} Połączono z {label}")
    return conn

def insert(conn, node_label, payload):
    chk = checksum(payload)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLE} (written_by, payload, checksum) "
            f"VALUES (%s, %s, %s) RETURNING id",
            (node_label, payload, chk)
        )
        row = cur.fetchone()
        return row[0], chk

def read_by_id(conn, rec_id, retries=8, delay=0.1):
    for _ in range(retries):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {TABLE} WHERE id = %s", (rec_id,))
            row = cur.fetchone()
            if row:
                return row
        time.sleep(delay)
    return None

def all_rows(conn):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT id, written_by, payload, checksum FROM {TABLE} ORDER BY id")
        return cur.fetchall()

def print_header():
    print(f"""
{BLD}{CYN}╔══════════════════════════════════════════════════════════╗
║     YugabyteDB — Test BIGSERIAL (integer PK) multi-master ║
╚══════════════════════════════════════════════════════════╝{RST}
""")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cleanup", action="store_true", help="Nie usuwaj danych po teście")
    parser.add_argument("--rows", type=int, default=10, metavar="N",
                        help="Liczba wierszy do zapisania NA KAŻDY node (default: 10)")
    args = parser.parse_args()
    print_header()
    conn1 = connect(NODE1, "node1 (port 5433)")
    conn2 = connect(NODE2, "node2 (port 5434)")

    ROUNDS = args.rows
    print(f"  Wierszy na node: {ROUNDS}  (łącznie: {ROUNDS*2})")
    print(f"\n{BLD}  Faza 1: Naprzemienne zapisy ({ROUNDS} rund × 2 nody){RST}")
    print(f"{CYN}  {'Node':^8} {'ID wygenerowane':^20} {'Widoczne na':^14} {'Latencja':^12} {'Status'}{RST}")
    print(f"  {'─'*65}")

    ids_node1, ids_node2 = [], []

    for i in range(1, ROUNDS + 1):
        # Zapis na node1
        payload1 = rand_payload()
        t0 = time.perf_counter()
        id1, chk1 = insert(conn1, "node1", payload1)
        ids_node1.append(id1)
        row = read_by_id(conn2, id1)           # odczyt z node2
        lat = (time.perf_counter() - t0) * 1000
        ok = row and row["checksum"] == chk1
        status = f"{GRN}✅ OK{RST}" if ok else f"{RED}❌ FAIL{RST}"
        print(f"  {'node1':^8} {id1:^20} {'node2':^14} {lat:>8.1f} ms   {status}")

        # Zapis na node2
        payload2 = rand_payload()
        t0 = time.perf_counter()
        id2, chk2 = insert(conn2, "node2", payload2)
        ids_node2.append(id2)
        row = read_by_id(conn1, id2)           # odczyt z node1
        lat = (time.perf_counter() - t0) * 1000
        ok = row and row["checksum"] == chk2
        status = f"{GRN}✅ OK{RST}" if ok else f"{RED}❌ FAIL{RST}"
        print(f"  {'node2':^8} {id2:^20} {'node1':^14} {lat:>8.1f} ms   {status}")

    # ── Analiza sekwencji ─────────────────────────────────────────
    print(f"\n{BLD}  Faza 2: Analiza ID wygenerowanych przez każdy node{RST}")
    print(f"\n  IDs z node1: {sorted(ids_node1)}")
    print(f"  IDs z node2: {sorted(ids_node2)}")

    all_ids = sorted(ids_node1 + ids_node2)
    gaps = [all_ids[i+1] - all_ids[i] for i in range(len(all_ids)-1) if all_ids[i+1] - all_ids[i] > 1]
    print(f"\n  Wszystkie IDs (posortowane): {all_ids}")
    print(f"  Ciągłość sekwencji: ", end="")
    if not gaps:
        print(f"{GRN}✅ Brak luk (sekwencja ciągła){RST}")
    else:
        print(f"{YEL}⚠️  Luki w sekwencji w miejscach: {gaps}{RST}")
        print(f"     (normalne w distributed DB — każdy node ma swój zakres sekwencji)")

    # ── Pełny widok z obu nodów ───────────────────────────────────
    print(f"\n{BLD}  Faza 3: Pełna tabela widziana z node1 vs node2{RST}")
    rows1 = all_rows(conn1)
    rows2 = all_rows(conn2)

    print(f"\n  Wierszy na node1: {len(rows1)}")
    print(f"  Wierszy na node2: {len(rows2)}")

    if len(rows1) == len(rows2):
        print(f"  {GRN}✅ Liczba wierszy zgodna — replikacja kompletna{RST}")
    else:
        print(f"  {RED}❌ Różna liczba wierszy! node1={len(rows1)}, node2={len(rows2)}{RST}")

    ids1_set = {r["id"] for r in rows1}
    ids2_set = {r["id"] for r in rows2}
    if ids1_set == ids2_set:
        print(f"  {GRN}✅ Zbiory ID identyczne na obu nodach{RST}")
    else:
        diff = ids1_set.symmetric_difference(ids2_set)
        print(f"  {RED}❌ Różne IDs: {diff}{RST}")

    # Tabela wynikowa
    print(f"\n  {'ID':>6} {'written_by':^10} {'payload':^35} {'checksum':^14}")
    print(f"  {'─'*70}")
    for r in rows1:
        print(f"  {r['id']:>6} {r['written_by']:^10} {r['payload']:^35} {r['checksum']:^14}")

    # Cleanup
    print()
    if args.no_cleanup:
        print(f"  {YEL}--no-cleanup: dane pozostają w tabeli '{TABLE}'.{RST}")
        print(f"  Sprawdź: SELECT * FROM {TABLE} ORDER BY id;")
    else:
        with conn1.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE}")
        print(f"  {CYN}Dane wyczyszczone. Użyj --no-cleanup żeby je zachować.{RST}")

    conn1.close()
    conn2.close()

if __name__ == "__main__":
    main()
