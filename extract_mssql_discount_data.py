#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extract discount data from MSSQL and save to CSV.
Run this on your local machine.

Usage:
    python extract_mssql_discount_data.py
"""
import pyodbc
import pandas as pd
from datetime import datetime, timedelta
import sys

# Connection parameters
SERVER = "43.133.116.56,14369"
USERNAME = "ro_hqosh"
PASSWORD = "R3ad@osh"
DATABASE = "HQ_Oshinei"  # or HQ_OSNLAO

print("=" * 80)
print("MSSQL Discount Data Extraction")
print("=" * 80)
print(f"Server: {SERVER}")
print(f"Database: {DATABASE}")
print(f"User: {USERNAME}\n")

try:
    # Connect to MSSQL
    conn_str = f"Driver={{ODBC Driver 17 for SQL Server}};Server={SERVER};Database={DATABASE};UID={USERNAME};PWD={PASSWORD};Encrypt=no;TrustServerCertificate=yes"

    print("Connecting to MSSQL...")
    conn = pyodbc.connect(conn_str, timeout=30)
    cursor = conn.cursor()
    print("✓ Connected!\n")

    # Step 1: List all tables
    print("=" * 80)
    print("STEP 1: Available Tables")
    print("=" * 80)
    cursor.execute("""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME
    """)
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Total tables: {len(tables)}\n")
    for i, table in enumerate(tables, 1):
        print(f"  {i:2}. {table}")

    # Step 2: Identify discount/sales related tables
    print("\n" + "=" * 80)
    print("STEP 2: Discount/Sales Related Tables")
    print("=" * 80)
    discount_tables = [t for t in tables if any(x in t.upper() for x in ['DISCOUNT', 'COUPON', 'BILL', 'RECEIPT', 'SALE', 'POS', 'TRANSACTION'])]

    for table in discount_tables:
        print(f"\n📋 {table}")
        cursor.execute(f"SELECT TOP 1 * FROM {table}")
        cols = [desc[0] for desc in cursor.description]
        print(f"   Columns ({len(cols)}): {', '.join(cols)}")

        # Count rows
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"   Rows: {count:,}")

    # Step 3: Query discount transactions (customize based on table structure)
    print("\n" + "=" * 80)
    print("STEP 3: Extracting Discount Transactions")
    print("=" * 80)

    # Try multiple possible queries based on common Easy POS schema
    queries = [
        # Query 1: Common Easy POS schema
        """
        SELECT TOP 5000
            b.BranchID,
            b.BranchName,
            rt.ReceiptNo,
            rt.SaleDate,
            rt.CashierName,
            rt.DiscountAmount,
            rt.DiscountReason,
            rt.CouponCode
        FROM Receipt_Transaction rt
        JOIN Branch b ON rt.BranchID = b.BranchID
        WHERE rt.SaleDate >= DATEADD(day, -62, GETDATE())
          AND rt.DiscountAmount > 0
        ORDER BY rt.SaleDate DESC
        """,

        # Query 2: Alternative schema
        """
        SELECT TOP 5000
            bill.branch_id,
            bill.receipt_no,
            bill.sale_date,
            bill.cashier,
            bill.discount_amount,
            bill.discount_type,
            bill.coupon_code
        FROM bill_header bill
        WHERE bill.sale_date >= DATEADD(day, -62, GETDATE())
          AND bill.discount_amount > 0
        ORDER BY bill.sale_date DESC
        """,

        # Query 3: Simple discount table
        """
        SELECT TOP 5000
            *
        FROM discount_transaction
        WHERE sale_date >= DATEADD(day, -62, GETDATE())
        ORDER BY sale_date DESC
        """
    ]

    discount_data = None
    for i, query in enumerate(queries, 1):
        try:
            print(f"\nTrying query {i}...")
            df = pd.read_sql(query, conn)
            if not df.empty:
                print(f"✓ Got {len(df)} rows!")
                discount_data = df
                break
            else:
                print(f"  (No results)")
        except Exception as e:
            print(f"  Query failed: {str(e)[:60]}")
            continue

    if discount_data is None:
        # Fallback: Try to find and export any discount-related table
        print("\n⚠️  Standard queries didn't work. Trying to export discount tables...\n")

        for table in discount_tables[:3]:
            try:
                print(f"Exporting {table}...")
                df = pd.read_sql(f"SELECT TOP 5000 * FROM {table}", conn)

                filename = f"discount_data_{table}.csv"
                df.to_csv(filename, index=False, encoding='utf-8-sig')
                print(f"✓ Saved: {filename} ({len(df)} rows)\n")

            except Exception as e:
                print(f"  Error: {e}\n")

    else:
        # Save discount data
        filename = f"discount_transactions_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        discount_data.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"\n✓ Saved: {filename}")
        print(f"  Rows: {len(discount_data)}")
        print(f"  Columns: {list(discount_data.columns)}")

    conn.close()
    print("\n" + "=" * 80)
    print("✓ Done! Send the CSV file to continue with analysis.")
    print("=" * 80)

except pyodbc.Error as e:
    print(f"\n✗ MSSQL Connection Error:")
    print(f"  {e}")
    print("\nMake sure:")
    print("  - ODBC Driver 17 for SQL Server is installed")
    print("  - Server IP/Port are correct")
    print("  - Credentials are valid")
    sys.exit(1)

except Exception as e:
    print(f"\n✗ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
