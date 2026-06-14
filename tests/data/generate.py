"""
tests/data/generate.py — Generate test CSV files.
Run: python tests/data/generate.py
"""

import csv
import random
import os

CATEGORIES = ["Food", "Tech", "Clothing", "Books", "Sports"]
REGIONS    = ["North", "South", "East", "West"]

def write_csv(filepath, rows):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓ {filepath} ({len(rows)} rows)")


def make_sales(n):
    return [
        {
            "id":       i + 1,
            "category": random.choice(CATEGORIES),
            "region":   random.choice(REGIONS),
            "amount":   round(random.uniform(10, 5000), 2),
            "date":     f"2026-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
        }
        for i in range(n)
    ]

def make_customers(n):
    return [
        {
            "id":   i + 1,
            "name": f"Customer_{i+1}",
            "age":  random.randint(18, 75),
            "city": random.choice(["Tunis", "Sfax", "Sousse", "Monastir"]),
        }
        for i in range(n)
    ]


if __name__ == "__main__":
    base = os.path.dirname(__file__)
    print("Generating test data...")
    write_csv(f"{base}/sales.csv",        make_sales(100))
    write_csv(f"{base}/sales_medium.csv", make_sales(10_000))
    write_csv(f"{base}/customers.csv",    make_customers(100))
    print("Done.")