"""Verify the seed script produces the right row counts and FK integrity."""

from __future__ import annotations

import sqlite3

from utils.config import get_settings


def test_ecommerce_row_counts(seeded_dbs):
    with sqlite3.connect(get_settings().ecommerce_db_path) as conn:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("categories", "customers", "products", "orders")
        }
    assert counts == {
        "categories": 5,
        "customers": 13,
        "products": 20,
        "orders": 25,
    }


def test_support_row_counts(seeded_dbs):
    with sqlite3.connect(get_settings().support_db_path) as conn:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("agents", "customers", "tickets", "interactions")
        }
    assert counts == {
        "agents": 6,
        "customers": 12,
        "tickets": 15,
        "interactions": 19,
    }


def test_ecommerce_foreign_key_integrity(seeded_dbs):
    """Every order's customer_id must exist in customers; every product's
    category_id must exist in categories."""
    with sqlite3.connect(get_settings().ecommerce_db_path) as conn:
        orphan_orders = conn.execute(
            "SELECT COUNT(*) FROM orders o "
            "WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)"
        ).fetchone()[0]
        orphan_products = conn.execute(
            "SELECT COUNT(*) FROM products p "
            "WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE c.id = p.category_id)"
        ).fetchone()[0]
    assert orphan_orders == 0
    assert orphan_products == 0


def test_support_foreign_key_integrity(seeded_dbs):
    with sqlite3.connect(get_settings().support_db_path) as conn:
        orphan_tickets = conn.execute(
            "SELECT COUNT(*) FROM tickets t "
            "WHERE NOT EXISTS (SELECT 1 FROM customers c WHERE c.id = t.customer_id)"
        ).fetchone()[0]
        orphan_interactions = conn.execute(
            "SELECT COUNT(*) FROM interactions i "
            "WHERE NOT EXISTS (SELECT 1 FROM tickets t WHERE t.id = i.ticket_id) "
            "   OR NOT EXISTS (SELECT 1 FROM agents   a WHERE a.id = i.agent_id)"
        ).fetchone()[0]
    assert orphan_tickets == 0
    assert orphan_interactions == 0


def test_cross_domain_overlap_exists(seeded_dbs):
    """Sanity check: some customers should appear in BOTH domains by email."""
    with sqlite3.connect(get_settings().ecommerce_db_path) as conn:
        ecom_emails = {r[0] for r in conn.execute("SELECT email FROM customers")}
    with sqlite3.connect(get_settings().support_db_path) as conn:
        support_emails = {r[0] for r in conn.execute("SELECT email FROM customers")}
    overlap = ecom_emails & support_emails
    assert len(overlap) >= 1, "expected at least one customer in both domains"
