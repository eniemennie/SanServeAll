"""
Shared product-name mapping for the client's raw-data import commands
that reference finished goods by the client's own catalog-reference
name: import_production_and_ingredient_usage (Production_Records.xlsx)
and import_stock_transfer (Stock_Transfer.xlsx). Both files draw their
"Product Name" column from the exact same 43-name universe (verified:
100% overlap), so this mapping lives in one place rather than being
copy-pasted and risking drift between the two commands.

See import_production_and_ingredient_usage's module docstring for the
full reasoning behind every entry here: cosmetic differences from the
real catalog name (apostrophe-size vs. "in", spacing), and the 6
size-variant Products added specifically for this client-data import
(scripts/seed_menu_catalog.py's seed_production_size_variants).
"""

# The one product name that represents two separate finished goods
# produced/moved together under a single recorded quantity -- maps the
# client's combined name to the (suffix, real Product name) pairs it
# actually represents. Every command that encounters this name must
# split its quantity across both (see each command's own docstring for
# why -- the specific ratio/suffix convention is the same everywhere).
COMBINED_PRODUCTS = {
    "Butter Toast and Broas (Jar)": [
        ("BT", "Butter Toast (Jar)"),
        ("BR", "Broas (Jar)"),
    ]
}

# Every other name in either file maps 1:1. Identity (name maps to
# itself) is assumed for any name not listed here.
PRODUCT_NAME_MAP = {
    "Almond (8' Round)": "Almond (8in Round)",
    "Pistachio (8' Round)": "Pistachio (8in Round)",
    "Choco Hazelnut (8' Round)": "Choco Hazelnut (8in Round)",
    "Matcha Pistachio (8' Round)": "Matcha Pistachio (8in Round)",
    "Classic Cashew (8' Round)": "Classic Cashew (8in Round)",
    "Mocha Walnut (8' Round)": "Mocha Walnut (8in Round)",
    "Choco Hazelnut (7' Square)": "Choco Hazelnut (7in Square)",
    "Matcha Pistachio (7' Square)": "Matcha Pistachio (7in Square)",
    "Almond (7' Square)": "Almond (7in Square)",
    "Pistachio (7' Square)": "Pistachio (7in Square)",
    "Classic Cashew (7' Square)": "Classic Cashew (7in Square)",
    "Mocha Walnut (7' Square)": "Mocha Walnut (7in Square)",
    "Mixed Nuts (200 g)": "Mixed Nuts (200g)",
    "Original Caramel Bites (Big 400 g)": "Original Caramel Bites (Big 400g)",
    "Salted Caramel Bites (Big 400 g)": "Salted Caramel Bites (Big 400g)",
    "Salted Caramel Bites (Small 200 g)": "Salted Caramel Bites (Small 200g)",
    "Original Caramel Bites (Small 200 g)": "Original Caramel Bites (Small 200g)",
}


def resolve_product_name(client_name):
    """The real catalog name for a non-combined client-file name."""
    return PRODUCT_NAME_MAP.get(client_name, client_name)


def split_evenly(quantity, n):
    """Splits an integer `quantity` into `n` integer parts that sum back
    to it EXACTLY -- unlike plain `quantity // n`, which silently drops
    the remainder for an odd quantity (e.g. 13 // 2 = 6 + 6 = 12, losing
    a real unit). Stock_Transfer.xlsx's combined-batch rows include
    exactly this case (13 and 15 total units across 2 products), so
    exactness matters here, not just evenness. Any remainder goes to the
    earliest part(s) -- an arbitrary but deterministic, documented
    convention, same as the other combined-batch assumptions."""
    base = quantity // n
    remainder = quantity - base * n
    return [base + 1 if i < remainder else base for i in range(n)]
