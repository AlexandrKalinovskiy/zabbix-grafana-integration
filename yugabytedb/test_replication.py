#!/usr/bin/env python3
"""
YugabyteDB Multi-Master Replication Test
Testuje zapis na node1→odczyt z node2 i node2→odczyt z node1 w pętli.

Wymagania:
    pip install psycopg2-binary

Użycie:
    python3 test_replication.py           # 20 iteracji (domyślnie)
    python3 test_replication.py --loops 100
    python3 test_replication.py --loops 0  # tryb ciągły (Ctrl+C aby zatrzymać)
    python3 test_replication.py --delay 0.5  # pauza między iteracjami [s]
"""

import argparse
import hashlib
import random
import signal
import string
import sys
import time
import uuid
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("❌  Brak modułu psycopg2. Zainstaluj: pip install psycopg2-binary")
    sys.exit(1)

# ─── Konfiguracja ────────────────────────────────────────────────
NODE1 = {"host": "192.168.40.60", "port": 5433, "database": "yugabyte",
         "user": "yugabyte", "password": "yugabyte", "connect_timeout": 5}
NODE2 = {"host": "192.168.40.60", "port": 5434, "database": "yugabyte",
         "user": "yugabyte", "password": "yugabyte", "connect_timeout": 5}

TABLE = "replication_test"

# ─── Kolory ANSI ─────────────────────────────────────────────────
GRN  = "\033[92m"
RED  = "\033[91m"
YEL  = "\033[93m"
BLU  = "\033[94m"
CYN  = "\033[96m"
GRY  = "\033[90m"
BLD  = "\033[1m"
RST  = "\033[0m"

# ─── Statystyki ──────────────────────────────────────────────────
stats = {
    "total":    0,
    "passed":   0,
    "failed":   0,
    "errors":   0,
    "latency":  [],
}

running = True


def signal_handler(sig, frame):
    global running
    running = False
    print(f"\n{YEL}⚡ Przerwano przez użytkownika (Ctrl+C){RST}")


signal.signal(signal.SIGINT, signal_handler)


# ─── Pomocnicze ──────────────────────────────────────────────────
def rand_payload(n=40):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def checksum(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def connect(cfg: dict, label: str):
    try:
        conn = psycopg2.connect(**cfg)
        conn.autocommit = True
        return conn
    except psycopg2.OperationalError as e:
        print(f"{RED}✗ Nie można połączyć się z {label}: {e}{RST}")
        return None


def setup_table(conn, label: str):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                run_id      TEXT        NOT NULL,
                iteration   INT         NOT NULL,
                written_by  TEXT        NOT NULL,
                payload     TEXT        NOT NULL,
                checksum    TEXT        NOT NULL,
                written_at  TIMESTAMPTZ DEFAULT now()
            )
        """)
    print(f"{GRY}  Tabela '{TABLE}' gotowa na {label}{RST}")


def write_record(conn, run_id: str, iteration: int, node_label: str, payload: str) -> str:
    rec_id = str(uuid.uuid4())
    chk = checksum(payload)
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {TABLE}
                    (id, run_id, iteration, written_by, payload, checksum)
                VALUES (%s, %s, %s, %s, %s, %s)""",
            (rec_id, run_id, iteration, node_label, payload, chk)
        )
    return rec_id, chk


def read_record(conn, rec_id: str, retries: int = 5, delay: float = 0.1):
    """Próbuje odczytać rekord z opcjonalnym retry (replikacja może chwilę trwać)."""
    for attempt in range(retries):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT * FROM {TABLE} WHERE id = %s",
                (rec_id,)
            )
            row = cur.fetchone()
            if row:
                return row
        time.sleep(delay)
    return None


def print_header():
    print(f"""
{BLD}{CYN}╔══════════════════════════════════════════════════════════╗
║         YugabyteDB — Test replikacji multi-master        ║
╠══════════════════════════════════════════════════════════╣
║  Node1: 192.168.40.60:5433                               ║
║  Node2: 192.168.40.60:5434                               ║
╚══════════════════════════════════════════════════════════╝{RST}
""")


def print_result(iteration: int, write_node: str, read_node: str,
                 ok: bool, latency_ms: float, rec_id: str,
                 error: str = ""):
    status = f"{GRN}✅ OK{RST}" if ok else f"{RED}❌ FAIL{RST}"
    lat    = f"{latency_ms:6.1f} ms"
    short_id = rec_id[:8] + "..." if rec_id else "N/A"
    print(
        f"  [{iteration:>4}] {BLD}{write_node}{RST}→{BLD}{read_node}{RST} "
        f"| {status} | {lat} | id={short_id}"
        + (f" | {RED}{error}{RST}" if error else "")
    )


def print_stats():
    total   = stats["total"]
    passed  = stats["passed"]
    failed  = stats["failed"]
    errors  = stats["errors"]
    lats    = stats["latency"]

    avg_lat = sum(lats) / len(lats) if lats else 0
    max_lat = max(lats) if lats else 0
    min_lat = min(lats) if lats else 0
    pct_ok  = (passed / total * 100) if total else 0

    print(f"""
{BLD}{CYN}══════════════════ PODSUMOWANIE ══════════════════{RST}
  Iteracje łącznie : {total}
  {GRN}Zaliczone        : {passed}{RST}
  {RED}Niezaliczone     : {failed}{RST}
  {YEL}Błędy połączeń   : {errors}{RST}
  Skuteczność      : {BLD}{pct_ok:.1f}%{RST}

  Latencja replikacji (odczyt z drugiego noda):
    Średnia : {avg_lat:.1f} ms
    Min     : {min_lat:.1f} ms
    Max     : {max_lat:.1f} ms
{BLD}{CYN}══════════════════════════════════════════════════{RST}
""")


def run_iteration(conn1, conn2, run_id: str, i: int, delay: float):
    """
    Jedna iteracja = 2 testy:
      A) Zapis na node1, odczyt z node2
      B) Zapis na node2, odczyt z node1
    """
    for write_conn, read_conn, wlabel, rlabel in [
        (conn1, conn2, "node1", "node2"),
        (conn2, conn1, "node2", "node1"),
    ]:
        stats["total"] += 1
        payload = rand_payload()
        rec_id  = None
        ok      = False
        error   = ""
        t0      = time.perf_counter()

        try:
            rec_id, expected_chk = write_record(
                write_conn, run_id, i, wlabel, payload
            )
            row = read_record(read_conn, rec_id)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            if row is None:
                error = "Rekord nie pojawił się na drugim nodzie"
            elif row["checksum"] != expected_chk:
                error = f"Checksum mismatch! got={row['checksum']} want={expected_chk}"
            elif row["payload"] != payload:
                error = "Payload nie zgadza się!"
            else:
                ok = True

        except psycopg2.Error as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            error = str(e).strip().splitlines()[0]
            stats["errors"] += 1

        stats["latency"].append(elapsed_ms)
        if ok:
            stats["passed"] += 1
        else:
            stats["failed"] += 1

        print_result(i, wlabel, rlabel, ok, elapsed_ms,
                     rec_id or "", error)
        time.sleep(delay)


def cleanup(conn1, conn2, run_id: str):
    """Usuwa rekordy z tego uruchomienia."""
    for conn, label in [(conn1, "node1"), (conn2, "node2")]:
        try:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TABLE} WHERE run_id = %s", (run_id,))
            print(f"{GRY}  Wyczyszczono dane run_id={run_id[:8]}... na {label}{RST}")
        except Exception:
            pass


# ─── Main ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="YugabyteDB replication test")
    parser.add_argument("--loops", type=int, default=20,
                        help="Liczba iteracji (0 = nieskończony)")
    parser.add_argument("--delay", type=float, default=0.2,
                        help="Pauza między testami [s] (default: 0.2)")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="Nie usuwaj danych testowych po zakończeniu")
    args = parser.parse_args()

    print_header()

    # Połączenia
    conn1 = connect(NODE1, "node1 (port 5433)")
    conn2 = connect(NODE2, "node2 (port 5434)")
    if not conn1 or not conn2:
        sys.exit(1)

    # Przygotowanie tabeli (tylko na node1 — replika pojawi się na node2)
    setup_table(conn1, "node1")
    time.sleep(0.5)  # daj chwilę na replikację DDL

    run_id = str(uuid.uuid4())
    mode   = "ciągły (Ctrl+C aby zatrzymać)" if args.loops == 0 else f"{args.loops} iteracji"
    print(f"\n{BLD}  Run ID : {run_id[:8]}...{RST}")
    print(f"  Tryb   : {mode}")
    print(f"  Delay  : {args.delay}s\n")
    print(f"{GRY}  {'Iter':>6}  Kierunek          Status    Latencja   Record ID{RST}")
    print(f"{GRY}  {'─'*70}{RST}")

    i = 0
    try:
        while running:
            i += 1
            run_iteration(conn1, conn2, run_id, i, args.delay)
            if args.loops > 0 and i >= args.loops:
                break
    finally:
        print()
        if not args.no_cleanup:
            cleanup(conn1, conn2, run_id)
        print_stats()
        conn1.close()
        conn2.close()


if __name__ == "__main__":
    main()
