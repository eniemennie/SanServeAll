"""
Seeds the real Jorge's Casa de Sans Rival product catalog from the
owner-supplied menu (two reference images, Oct 2026). Loads Product
rows only -- not Inventory (per-branch stock), which is a separate,
branch-by-branch decision the owner makes later.

Deliberately excludes the All Day Breakfast section, per explicit
instruction.

Real structural decisions made here, not invented casually -- see the
accompanying PR description for the full reasoning:

- Coffee (Hot/Iced): the Product model has no temperature field, only
  a Small/Large price pair, and Hot vs. Iced isn't a size. Loaded as
  two separate products per drink ("Americano (Hot)" /
  "Americano (Iced)") rather than overloading the Large-price field
  with a meaning it doesn't have.
- Sans Rival Cakes (4 sizes: Mini/Solo/7in Square/8in Round): the
  Product model only holds 2 price tiers, but this menu has 4. Rather
  than silently drop 2 of them, each size is loaded as its own
  product per flavor (24 real SKUs) -- also a more honest model for
  inventory, since a Mini and an 8in Round are genuinely
  separately-stocked items, not one item with a size option.
- Silvanas and Soft & Fluffy (box vs. per-piece): same reasoning,
  loaded as separate box-size products. Per-piece-by-flavor pricing
  for Silvanas is skipped, since it duplicates what the Mini cakes
  already price per flavor.
- Butter Toast and Broas: the source menu image shows one price
  ("Jar - P140.00") under two product photos. Interpreted as two
  separate P140 jar products (flagged as an assumption, not a
  certainty, when this script was delivered) rather than silently
  guessing a combined price with no basis for it.

Safe to run more than once -- every row uses get_or_create/
update_or_create, matching seed_demo_data.py's own convention.
"""

from apps.inventory.models import Product
from apps.pos.models import AddOn

Category = Product.Category


def _product(name, price, category, large_price=None, is_best_seller=False):
    defaults = {
        "price": price,
        "category": category,
        "is_best_seller": is_best_seller,
        "product_type": Product.ProductType.FINISHED_GOOD,
    }
    if large_price is not None:
        defaults["large_price"] = large_price
    Product.objects.update_or_create(name=name, defaults=defaults)


def seed_coffee():
    # (base name, Hot price, Iced price)
    drinks = [
        ("Americano", "80.00", "90.00"),
        ("Cafe Latte", "130.00", "140.00"),
        ("Spanish Latte", "150.00", "160.00"),
        ("Mocha Latte", "150.00", "160.00"),
        ("Caramel Machiatto", "150.00", "160.00"),
        ("Salted Caramel Machiatto", "150.00", "160.00"),
        ("Matcha Latte", "140.00", "150.00"),
    ]
    for name, hot, iced in drinks:
        _product(f"{name} (Hot)", hot, Category.COFFEE)
        _product(f"{name} (Iced)", iced, Category.COFFEE)


def seed_sea_salt_series():
    for name, price in [
        ("Sea Salt Latte", "170.00"),
        ("Spanish Sea Salt Latte", "170.00"),
        ("Matcha Sea Salt Latte", "170.00"),
        ("Biscoff Sea Salt Latte", "190.00"),
    ]:
        _product(name, price, Category.SEA_SALT_SERIES)


def seed_coffee_frappe():
    for name, price in [
        ("Caramel Machiatto Frappe", "180.00"),
        ("Salted Caramel Machiatto Frappe", "180.00"),
        ("Java Chip", "185.00"),
        ("Dark Cookies & Cream Frappe", "185.00"),
        ("Mocha Frappe", "180.00"),
    ]:
        _product(name, price, Category.COFFEE_FRAPPE)


def seed_non_coffee_frappe():
    for name, price in [
        ("Chocolate Frappe", "170.00"),
        ("Strawberry Cream Frappe", "170.00"),
        ("Caramel Frappe", "160.00"),
        ("Salted Caramel Frappe", "160.00"),
        ("Dark Cookies & Cream (Non-Coffee)", "170.00"),
        ("Matcha Frappe", "180.00"),
    ]:
        _product(name, price, Category.NON_COFFEE_FRAPPE)


def seed_specialty_drinks():
    for name, price in [
        ("Chocolate Ice Shaken", "130.00"),
        ("Strawberry Ice Shaken", "130.00"),
        ("Ube Ice Shaken", "130.00"),
        ("Biscoff Latte", "170.00"),
        ("Strawberry Matcha", "170.00"),
        ("House Blend Iced Tea", "80.00"),
        ("Hot Chocolate", "120.00"),
    ]:
        _product(name, price, Category.SPECIALTY)


def seed_addons():
    for name, price in [
        ("Extra Espresso Shot", "40.00"),
        ("Oat Milk", "60.00"),
        ("Extra Pump of Sauce", "30.00"),
        ("Extra Pump of Syrup", "20.00"),
        ("Extra Condense (25ml)", "15.00"),
        ("Extra Condense (40ml)", "25.00"),
    ]:
        AddOn.objects.update_or_create(name=name, defaults={"price": price, "is_active": True})


def seed_pasta():
    _product("Chicken Pesto", "270.00", Category.PASTA)
    _product("Creamy Truffle Pasta", "270.00", Category.PASTA)
    _product("Shrimp Marinara", "290.00", Category.PASTA)
    _product("Lasagna", "290.00", Category.PASTA, is_best_seller=True)


def seed_desserts():
    for name, price in [
        ("Classic Tiramisu", "190.00"),
        ("Pistachio Tiramisu", "230.00"),
        ("Biscoff Banoffee Pie", "210.00"),
        ("Brownies", "45.00"),
        ("Revel Bar", "45.00"),
    ]:
        _product(name, price, Category.DESSERTS)


def seed_sans_rival_cakes():
    # (flavor, Mini, Solo, 7in Square, 8in Round, is_best_seller)
    cakes = [
        ("Classic Cashew", "95.00", "230.00", "500.00", "840.00", True),
        ("Almond", "105.00", "240.00", "520.00", "860.00", False),
        ("Mocha Walnut", "105.00", "240.00", "520.00", "860.00", False),
        ("Pistachio", "125.00", "270.00", "595.00", "1020.00", True),
        ("Choco Hazelnut", "135.00", "270.00", "605.00", "1050.00", False),
        ("Matcha Pistachio", "125.00", "270.00", "595.00", "1020.00", False),
    ]
    for flavor, mini, solo, square7, round8, best_seller in cakes:
        _product(f"{flavor} (Mini)", mini, Category.CAKES, is_best_seller=best_seller)
        _product(f"{flavor} (Solo)", solo, Category.CAKES, is_best_seller=best_seller)
        _product(f"{flavor} (7in Square)", square7, Category.CAKES, is_best_seller=best_seller)
        _product(f"{flavor} (8in Round)", round8, Category.CAKES, is_best_seller=best_seller)


def seed_silvanas():
    _product("Silvanas (Box of 10)", "380.00", Category.CAKES, is_best_seller=True)
    _product("Silvanas (Box of 20)", "760.00", Category.CAKES, is_best_seller=True)


def seed_soft_and_fluffy():
    _product("Classic Ensaymada (Piece)", "55.00", Category.BAKERY, is_best_seller=True)
    _product("Classic Ensaymada (Box of 4)", "210.00", Category.BAKERY, is_best_seller=True)
    _product("Cheeserolls (Piece)", "45.00", Category.BAKERY)
    _product("Cheeserolls (Box of 6)", "260.00", Category.BAKERY)
    _product(
        "Mixed Box (3 Cheeserolls + 2 Ensaymada)",
        "220.00",
        Category.BAKERY,
    )


def seed_bakery_corner():
    _product(
        "Original Caramel Bites",
        "105.00",
        Category.BAKERY,
        large_price="200.00",
        is_best_seller=True,
    )
    _product(
        "Salted Caramel Bites",
        "105.00",
        Category.BAKERY,
        large_price="200.00",
        is_best_seller=True,
    )
    _product("Mixed Nuts (200g)", "200.00", Category.BAKERY)
    _product("Lengua de Gato (Small Jar)", "105.00", Category.BAKERY)
    # Interpreted as two separate P140 jar products -- the source menu
    # image showed one price line under two product photos with no
    # clearer breakdown. Flag/confirm with the owner if this is wrong.
    _product("Butter Toast (Jar)", "140.00", Category.BAKERY)
    _product("Broas (Jar)", "140.00", Category.BAKERY)
    _product(
        "Creamy Leche Flan (Tub)",
        "150.00",
        Category.BAKERY,
        large_price="250.00",
        is_best_seller=True,
    )
    _product("Banana Bread", "60.00", Category.BAKERY)
    _product("Tea Cookies (Small)", "105.00", Category.BAKERY, large_price="180.00")


def run():
    seed_coffee()
    seed_sea_salt_series()
    seed_coffee_frappe()
    seed_non_coffee_frappe()
    seed_specialty_drinks()
    seed_addons()
    seed_pasta()
    seed_desserts()
    seed_sans_rival_cakes()
    seed_silvanas()
    seed_soft_and_fluffy()
    seed_bakery_corner()

    total_products = Product.objects.count()
    total_addons = AddOn.objects.count()
    print(f"Menu catalog seeded: {total_products} products, {total_addons} add-ons.")
    print("Note: All Day Breakfast was deliberately excluded.")


if __name__ == "__main__":
    run()
