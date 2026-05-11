-- E-commerce domain schema.
-- Matches the columns of the provided ecom_*.csv files exactly.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS customers (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    email    TEXT    NOT NULL UNIQUE,    -- cross-domain join key
    location TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    description TEXT,
    price       REAL    NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    order_date   TEXT    NOT NULL,        -- ISO yyyy-mm-dd, kept as text for SQLite
    total_amount REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_customer    ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_date        ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_products_category  ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_customers_email    ON customers(email);
