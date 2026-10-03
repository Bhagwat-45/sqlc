"""
data_generation.py  (pymssql version - no ODBC driver / Homebrew needed)
------------------------------------------------------------------------
Pulls data from the free DummyJSON API (https://dummyjson.com, no API key) and
loads it into a SQL Server database running from your docker-compose file.

Schema is designed for practising:
  * NULL functions  : ISNULL, COALESCE, NULLIF, IS NULL, CASE, NULL-safe joins
  * Window functions: ROW_NUMBER, RANK, DENSE_RANK, NTILE, LAG, LEAD,
                      running totals, moving averages, FIRST_VALUE/LAST_VALUE
  * CTEs            : plain, chained, and RECURSIVE (employee hierarchy)
  * Subqueries      : scalar, correlated, IN / NOT IN, EXISTS / NOT EXISTS, derived tables

Setup
-----
    pip3 install pymssql requests

    export MSSQL_SA_PASSWORD='YourStrong!Passw0rd'     # same one used in docker compose
    docker compose up -d
    python3 data_generation.py

Optional env vars: MSSQL_HOST (localhost), MSSQL_PORT (1433), MSSQL_DB (practice_db),
                   NUM_ORDERS (3000), RANDOM_SEED (42)
"""

import os
import random
import re
import sys
import time
from datetime import date, timedelta

import pymssql
import requests

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
HOST = os.getenv("MSSQL_HOST", "localhost")
PORT = int(os.getenv("MSSQL_PORT", "1433"))
DB_NAME = os.getenv("MSSQL_DB", "practice_db")
SA_PASSWORD = os.getenv("MSSQL_SA_PASSWORD", "TheGentleman2")
NUM_ORDERS = int(os.getenv("NUM_ORDERS", "3000"))
SEED = int(os.getenv("RANDOM_SEED", "42"))
NUM_EMPLOYEES = 60

API = "https://dummyjson.com"
TODAY = date.today()

random.seed(SEED)

if not SA_PASSWORD:
    sys.exit("Set MSSQL_SA_PASSWORD in your environment first.")
if not re.fullmatch(r"[A-Za-z0-9_]+", DB_NAME):
    sys.exit("MSSQL_DB may only contain letters, numbers and underscores.")


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def fetch_all(endpoint: str, key: str) -> list[dict]:
    """GET /endpoint?limit=0 returns every record on DummyJSON."""
    url = f"{API}/{endpoint}?limit=0"
    for attempt in range(1, 4):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            data = r.json()[key]
            print(f"  fetched {len(data):>4} rows from /{endpoint}")
            return data
        except requests.RequestException as exc:
            print(f"  attempt {attempt} failed for {url}: {exc}")
            time.sleep(2 * attempt)
    sys.exit(f"Could not fetch {url}")


def parse_loose_date(s: str | None) -> date | None:
    """DummyJSON birthDate looks like '1996-5-30' (not zero padded)."""
    if not s:
        return None
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def parse_iso_date(s: str | None) -> date | None:
    """'2025-04-30T09:41:02.053Z' -> date(2025, 4, 30)"""
    return date.fromisoformat(s[:10]) if s else None


def rand_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def maybe_null(value, pct: float):
    """Return None with probability `pct`, otherwise the value."""
    return None if random.random() < pct else value


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
DROP_ORDER = ["product_reviews", "order_items", "orders", "employees", "products", "customers"]

DDL = [
    """
    CREATE TABLE customers (
        customer_id   INT           NOT NULL PRIMARY KEY,
        first_name    NVARCHAR(50)  NOT NULL,
        last_name     NVARCHAR(50)  NOT NULL,
        maiden_name   NVARCHAR(50)  NULL,      -- mix of NULL and '' (practice NULLIF)
        email         NVARCHAR(120) NOT NULL,
        phone         NVARCHAR(40)  NULL,
        gender        NVARCHAR(10)  NULL,
        birth_date    DATE          NULL,
        city          NVARCHAR(80)  NULL,
        state         NVARCHAR(80)  NULL,
        country       NVARCHAR(80)  NULL,
        signup_date   DATE          NOT NULL,
        last_login    DATE          NULL        -- NULL = never logged in
    )""",
    """
    CREATE TABLE products (
        product_id    INT            NOT NULL PRIMARY KEY,
        title         NVARCHAR(200)  NOT NULL,
        category      NVARCHAR(80)   NOT NULL,
        brand         NVARCHAR(80)   NULL,       -- naturally missing in the API
        price         DECIMAL(10,2)  NOT NULL,
        discount_pct  DECIMAL(5,2)   NULL,
        rating        DECIMAL(3,2)   NULL,
        stock         INT            NULL,
        weight        DECIMAL(8,2)   NULL,
        sku           NVARCHAR(40)   NULL
    )""",
    """
    CREATE TABLE orders (
        order_id        INT           NOT NULL PRIMARY KEY,
        customer_id     INT           NOT NULL REFERENCES customers(customer_id),
        order_date      DATE          NOT NULL,
        ship_date       DATE          NULL,      -- NULL for Pending / Cancelled
        delivered_date  DATE          NULL,      -- NULL unless Delivered
        status          NVARCHAR(20)  NOT NULL,
        payment_method  NVARCHAR(30)  NULL,
        coupon_code     NVARCHAR(20)  NULL
    )""",
    """
    CREATE TABLE order_items (
        order_item_id INT IDENTITY(1,1) PRIMARY KEY,
        order_id      INT           NOT NULL REFERENCES orders(order_id),
        product_id    INT           NOT NULL REFERENCES products(product_id),
        quantity      INT           NOT NULL,
        unit_price    DECIMAL(10,2) NOT NULL,
        discount_pct  DECIMAL(5,2)  NULL         -- NULL = no discount applied
    )""",
    """
    CREATE TABLE employees (
        employee_id    INT            NOT NULL PRIMARY KEY,
        first_name     NVARCHAR(50)   NOT NULL,
        last_name      NVARCHAR(50)   NOT NULL,
        job_title      NVARCHAR(100)  NULL,
        department     NVARCHAR(80)   NULL,
        manager_id     INT            NULL REFERENCES employees(employee_id),  -- NULL = CEO
        hire_date      DATE           NOT NULL,
        salary         DECIMAL(10,2)  NULL,
        commission_pct DECIMAL(4,2)   NULL       -- only sales-type roles
    )""",
    """
    CREATE TABLE product_reviews (
        review_id      INT IDENTITY(1,1) PRIMARY KEY,
        product_id     INT           NOT NULL REFERENCES products(product_id),
        reviewer_name  NVARCHAR(100) NULL,
        reviewer_email NVARCHAR(120) NULL,
        rating         INT           NOT NULL,
        comment        NVARCHAR(500) NULL,
        review_date    DATE          NULL
    )""",
]


# --------------------------------------------------------------------------- #
# Database helpers (pymssql)
# --------------------------------------------------------------------------- #
def connect(database: str, autocommit: bool = False):
    return pymssql.connect(
        server=HOST,
        port=PORT,
        user="sa",
        password=SA_PASSWORD,
        database=database,
        login_timeout=10,
        autocommit=autocommit,
        charset="UTF-8",
    )


def wait_for_server(retries: int = 20) -> None:
    last_err = None
    for i in range(1, retries + 1):
        try:
            connect("master").close()
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            print(f"  waiting for SQL Server... ({i}/{retries})")
            time.sleep(5)
    sys.exit(f"SQL Server is not reachable. Is the container healthy?\nLast error: {last_err}")


def ensure_database() -> None:
    cn = connect("master", autocommit=True)  # CREATE DATABASE can't run in a transaction
    cn.cursor().execute(f"IF DB_ID('{DB_NAME}') IS NULL CREATE DATABASE [{DB_NAME}]")
    cn.close()


def bulk_insert(cur, table: str, cols: list[str], rows: list[tuple]) -> None:
    placeholders = ",".join(["%s"] * len(cols))  # pymssql uses %s, not ?
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    for i in range(0, len(rows), 1000):
        cur.executemany(sql, rows[i : i + 1000])
    print(f"  inserted {len(rows):>5} rows into {table}")


# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #
def build_customers(users: list[dict]) -> list[tuple]:
    rows = []
    for u in users:
        addr = u.get("address") or {}
        maiden = u.get("maidenName")
        # Keep '' for some, NULL for others -> great for NULLIF / COALESCE practice
        maiden_val = maiden if maiden else random.choice([None, ""])
        signup = rand_date(TODAY - timedelta(days=1100), TODAY - timedelta(days=30))
        rows.append((
            u["id"],
            u["firstName"],
            u["lastName"],
            maiden_val,
            u["email"],
            maybe_null(u.get("phone"), 0.10),
            maybe_null(u.get("gender"), 0.03),
            maybe_null(parse_loose_date(u.get("birthDate")), 0.05),
            maybe_null(addr.get("city"), 0.04),
            maybe_null(addr.get("state"), 0.06),
            addr.get("country"),
            signup,
            maybe_null(rand_date(signup, TODAY), 0.15),
        ))
    return rows


def build_products(products: list[dict]) -> list[tuple]:
    return [
        (
            p["id"],
            p["title"],
            p["category"],
            p.get("brand"),  # genuinely missing for some products
            p["price"],
            maybe_null(p.get("discountPercentage"), 0.10),
            p.get("rating"),
            maybe_null(p.get("stock"), 0.03),
            maybe_null(p.get("weight"), 0.08),
            p.get("sku"),
        )
        for p in products
    ]


def build_orders(customers: list[dict], products: list[dict]):
    """Synthetic orders built on top of REAL customers and REAL products."""
    # Only ~85% of customers / ~90% of products ever appear in orders,
    # so anti-joins (NOT EXISTS / LEFT JOIN ... IS NULL) return something.
    cust_ids = [c["id"] for c in random.sample(customers, int(len(customers) * 0.85))]
    prods = random.sample(products, int(len(products) * 0.90))

    statuses = ["Delivered", "Shipped", "Pending", "Cancelled", "Returned"]
    weights = [70, 10, 8, 7, 5]
    payments = ["Credit Card", "PayPal", "Debit Card", "Gift Card", "Bank Transfer"]
    coupons = ["WELCOME10", "SUMMER20", "FREESHIP", "VIP15", "BLACKFRI"]

    orders, items = [], []
    for order_id in range(1, NUM_ORDERS + 1):
        status = random.choices(statuses, weights)[0]
        order_date = rand_date(TODAY - timedelta(days=730), TODAY - timedelta(days=1))

        ship_date = delivered = None
        if status in ("Shipped", "Delivered", "Returned"):
            ship_date = order_date + timedelta(days=random.randint(1, 5))
        if status in ("Delivered", "Returned"):
            delivered = ship_date + timedelta(days=random.randint(1, 8))

        orders.append((
            order_id,
            random.choice(cust_ids),
            order_date,
            ship_date,
            delivered,
            status,
            maybe_null(random.choice(payments), 0.03),
            maybe_null(random.choice(coupons), 0.70),
        ))

        for p in random.sample(prods, random.randint(1, 5)):
            discount = p.get("discountPercentage") if random.random() < 0.6 else None
            items.append((order_id, p["id"], random.randint(1, 5), p["price"], discount))
    return orders, items


def build_employees(users: list[dict]) -> list[tuple]:
    """Ternary-tree hierarchy (CEO -> 3 reports -> 9 -> ...) for recursive CTEs."""
    staff = users[:NUM_EMPLOYEES]
    ids = [u["id"] for u in staff]
    sales_like = {"Sales", "Business Development", "Marketing"}
    rows = []
    for i, u in enumerate(staff):
        manager_id = None if i == 0 else ids[(i - 1) // 3]
        depth = 0 if i == 0 else (1 if i <= 3 else (2 if i <= 12 else 3))
        dept = (u.get("company") or {}).get("department")
        base = {0: 250000, 1: 150000, 2: 100000, 3: 65000}[depth]
        salary = round(base * random.uniform(0.85, 1.25), 2)
        commission = round(random.uniform(0.02, 0.15), 2) if dept in sales_like else None
        rows.append((
            u["id"],
            u["firstName"],
            u["lastName"],
            (u.get("company") or {}).get("title"),
            dept,
            manager_id,
            rand_date(TODAY - timedelta(days=3650), TODAY - timedelta(days=60)),
            maybe_null(salary, 0.03),
            commission,
        ))
    return rows


def build_reviews(products: list[dict]) -> list[tuple]:
    rows = []
    for p in products:
        for r in p.get("reviews", []):
            rows.append((
                p["id"],
                maybe_null(r.get("reviewerName"), 0.05),
                maybe_null(r.get("reviewerEmail"), 0.05),
                r["rating"],
                maybe_null(r.get("comment"), 0.10),
                parse_iso_date(r.get("date")),
            ))
    return rows


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    print("1) Fetching data from DummyJSON ...")
    users = fetch_all("users", "users")
    products = fetch_all("products", "products")

    print("2) Connecting to SQL Server ...")
    wait_for_server()
    ensure_database()
    cn = connect(DB_NAME)
    cur = cn.cursor()

    print("3) Recreating tables ...")
    for t in DROP_ORDER:
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    for stmt in DDL:
        cur.execute(stmt)
    cn.commit()

    print("4) Loading data ...")
    bulk_insert(cur, "customers",
                ["customer_id", "first_name", "last_name", "maiden_name", "email", "phone",
                 "gender", "birth_date", "city", "state", "country", "signup_date", "last_login"],
                build_customers(users))
    bulk_insert(cur, "products",
                ["product_id", "title", "category", "brand", "price", "discount_pct",
                 "rating", "stock", "weight", "sku"],
                build_products(products))

    orders, items = build_orders(users, products)
    bulk_insert(cur, "orders",
                ["order_id", "customer_id", "order_date", "ship_date", "delivered_date",
                 "status", "payment_method", "coupon_code"], orders)
    bulk_insert(cur, "order_items",
                ["order_id", "product_id", "quantity", "unit_price", "discount_pct"], items)
    bulk_insert(cur, "employees",
                ["employee_id", "first_name", "last_name", "job_title", "department",
                 "manager_id", "hire_date", "salary", "commission_pct"],
                build_employees(users))
    bulk_insert(cur, "product_reviews",
                ["product_id", "reviewer_name", "reviewer_email", "rating", "comment", "review_date"],
                build_reviews(products))
    cn.commit()

    print("5) Sanity check (NULL counts) ...")
    checks = {
        "customers.phone": "SELECT COUNT(*) FROM customers WHERE phone IS NULL",
        "customers.maiden_name = ''": "SELECT COUNT(*) FROM customers WHERE maiden_name = ''",
        "customers.last_login": "SELECT COUNT(*) FROM customers WHERE last_login IS NULL",
        "products.brand": "SELECT COUNT(*) FROM products WHERE brand IS NULL",
        "orders.ship_date": "SELECT COUNT(*) FROM orders WHERE ship_date IS NULL",
        "orders.coupon_code": "SELECT COUNT(*) FROM orders WHERE coupon_code IS NULL",
        "order_items.discount_pct": "SELECT COUNT(*) FROM order_items WHERE discount_pct IS NULL",
        "employees.commission_pct": "SELECT COUNT(*) FROM employees WHERE commission_pct IS NULL",
    }
    for label, sql in checks.items():
        cur.execute(sql)
        print(f"  NULLs in {label:<30}: {cur.fetchone()[0]}")

    cn.close()
    print(f"\nDone. Connect to  {HOST},{PORT}  database  [{DB_NAME}]  (user: sa)")


if __name__ == "__main__":
    main()