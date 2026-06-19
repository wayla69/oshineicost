#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Insert sample discount data for Ubon and Surin branches.

This populates the database with realistic discount transaction data
for testing the discount analysis report.
"""
import sqlite3
from datetime import datetime, timedelta
import random

def insert_sample_data():
    """Insert sample discount transactions."""
    conn = sqlite3.connect('oshinei.db')
    cursor = conn.cursor()

    # Insert branches
    branches = [
        (1, 'อุบลราชธานี'),
        (2, 'สุรินทร์'),
        (3, 'โคราช1'),
        (4, 'เชียงใหม่'),
        (5, 'อุดรธานี'),
    ]

    for branch_id, name in branches:
        try:
            cursor.execute("INSERT INTO branches (branch_id, name) VALUES (?, ?)",
                          (branch_id, name))
        except sqlite3.IntegrityError:
            pass

    # Insert categories
    categories = [
        (1, 'BUFFET'),
        (2, 'DRINK'),
        (3, 'SPECIAL'),
    ]

    for cat_id, name in categories:
        try:
            cursor.execute("INSERT INTO categories (category_id, name) VALUES (?, ?)",
                          (cat_id, name))
        except sqlite3.IntegrityError:
            pass

    # Sample cashiers and staff
    cashiers = [
        '02150002',  # กาญจนา เจริญยิ่ง (Asst. Manager)
        '02150003',  # ศิริวรรณ ศรีทัน (Supervisor)
        '02140002',  # สมชาย โทแช่ม (Branch Manager)
        'Cashier001',
        'Cashier002',
        'Cashier003',
        'Unknown',
    ]

    # Insert sample transactions (last 6 months)
    start_date = datetime.now() - timedelta(days=180)
    num_transactions = 200

    for i in range(num_transactions):
        # Distribute across branches and days
        branch_id = random.choice([1, 2])  # Ubon or Surin
        days_offset = random.randint(0, 180)
        sold_at = start_date + timedelta(days=days_offset, hours=random.randint(11, 21))

        cashier = random.choice(cashiers)

        # Food, drink, other subtotals
        food_subtotal = random.uniform(200, 2000)
        drink_subtotal = random.uniform(50, 500)
        other_subtotal = random.uniform(0, 200)

        # Create different discount patterns
        r = random.random()

        # Most transactions have no discount
        if r < 0.85:
            discount = 0
            sp_discount = 0
        # Some have normal discounts
        elif r < 0.95:
            # Normal discount (0.5 - 2.5%)
            discount = (food_subtotal + drink_subtotal) * random.uniform(0.005, 0.025)
            sp_discount = 0
        # Some have anomalous discounts (by specific staff)
        else:
            # High discounts associated with some staff
            if cashier in ['02150002', '02150003']:
                # These staff have higher discount rates
                discount = (food_subtotal + drink_subtotal) * random.uniform(0.05, 0.20)
                sp_discount = random.uniform(50, 300)
            else:
                discount = (food_subtotal + drink_subtotal) * random.uniform(0.01, 0.05)
                sp_discount = 0

        total_inc_vat = food_subtotal + drink_subtotal + other_subtotal - discount - sp_discount
        customer_count = random.randint(1, 8)

        receipt_no = f"RCP{branch_id}{sold_at.strftime('%Y%m%d')}{i:04d}"

        try:
            cursor.execute("""
                INSERT INTO sales_transaction
                (branch_id, receipt_no, sold_at, cashier, customer_count,
                 food_subtotal, drink_subtotal, other_subtotal,
                 discount, sp_discount, total_inc_vat, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                branch_id, receipt_no, sold_at.isoformat(), cashier, customer_count,
                food_subtotal, drink_subtotal, other_subtotal,
                discount, sp_discount, total_inc_vat, 'Easy'
            ))
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()

    print("✅ Sample discount data inserted successfully")
    print(f"   • Inserted ~{num_transactions} sample transactions")
    print(f"   • Branches: อุบลราชธานี, สุรินทร์")
    print(f"   • Cashiers: 7 (including 3 staff under investigation)")
    print(f"   • Period: Last 6 months")

if __name__ == "__main__":
    insert_sample_data()
