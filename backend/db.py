"""Database helpers. We use SQLite (a database stored in one file) and plain SQL."""
import sqlite3

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash

CATEGORIES = ["Supercars", "SUVs", "Sedans", "Small Personal Cars"]

# "IF NOT EXISTS" means running this twice is safe.
# Orders and payments tables will be added in the next step.
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin'))
);

CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS cars (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    make        TEXT NOT NULL,
    model       TEXT NOT NULL,
    year        INTEGER NOT NULL,
    price       INTEGER NOT NULL CHECK (price > 0),
    condition   TEXT NOT NULL DEFAULT 'Used' CHECK (condition IN ('New', 'Used')),
    specs       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'reserved', 'sold')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# (make, model, year, category, condition, price, specs)
SAMPLE_CARS = [
    ("Lamborghini", "Huracán", 2021, "Supercars", "Used", 38500000, "5.2L V10, 631 hp, AWD"),
    ("Ferrari", "F8 Tributo", 2022, "Supercars", "Used", 44000000, "3.9L V8 twin-turbo, 710 hp"),
    ("Toyota", "Land Cruiser Prado", 2020, "SUVs", "Used", 7800000, "2.8L diesel, 4WD, 7 seats"),
    ("Subaru", "Forester", 2019, "SUVs", "Used", 3200000, "2.0L petrol, AWD, CVT"),
    ("Mercedes-Benz", "C200", 2018, "Sedans", "Used", 4100000, "2.0L turbo, automatic"),
    ("Toyota", "Corolla", 2023, "Sedans", "New", 3600000, "1.8L hybrid, automatic"),
    ("Suzuki", "Swift", 2021, "Small Personal Cars", "Used", 1450000, "1.2L petrol, manual"),
    ("Mazda", "Demio", 2017, "Small Personal Cars", "Used", 980000, "1.3L petrol, automatic"),
]


def get_db():
    """Open one database connection per request and reuse it."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row  # lets us read columns by name
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(with_samples=True):
    db = get_db()
    db.executescript(SCHEMA)
    db.executemany("INSERT OR IGNORE INTO categories (name) VALUES (?)", [(c,) for c in CATEGORIES])
    if with_samples and db.execute("SELECT COUNT(*) FROM cars").fetchone()[0] == 0:
        for make, model, year, category, condition, price, specs in SAMPLE_CARS:
            db.execute(
                """INSERT INTO cars (category_id, make, model, year, price, condition, specs)
                   VALUES ((SELECT id FROM categories WHERE name = ?), ?, ?, ?, ?, ?, ?)""",
                (category, make, model, year, price, condition, specs),
            )
    db.commit()


def create_user(name, email, phone, password, role="customer"):
    """Add a user. The password is stored as a hash, never as plain text."""
    db = get_db()
    cursor = db.execute(
        "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        (name, email.lower(), phone, generate_password_hash(password), role),
    )
    db.commit()
    return cursor.lastrowid


# ---- Commands you can run in the terminal ----
@click.command("init-db")
def init_db_command():
    """Create the tables and sample cars."""
    init_db()
    click.echo("Database ready.")


@click.command("create-admin")
@click.option("--email", prompt=True)
@click.option("--name", prompt=True)
@click.option("--phone", prompt=True, help="e.g. 254712345678")
@click.password_option()
def create_admin_command(email, name, phone, password):
    """Create an admin account."""
    create_user(name, email, phone, password, role="admin")
    click.echo(f"Admin {email} created.")


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    app.cli.add_command(create_admin_command)
