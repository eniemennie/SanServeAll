"""Reusable number-formatting filters for currency and unit-aware
quantities (QA fix, adviser dry-run 2026-11-05 -- Issue #2).

Why not just `{{ value|floatformat:2|intcomma }}` inline everywhere:
that reads correctly, but repeating the two-filter chain (and
remembering it, rather than a bare `floatformat:2`) across every
currency display in the project is exactly how the original gap
happened -- `floatformat:2` alone was already used consistently
project-wide, it just never added a thousands separator, so every
amount above Php999.99 rendered as "1210142.87" instead of
"1,210,142.87". One filter (`peso`) that already does both, with a
name that reads clearly as "format this as money", is harder to
apply inconsistently than remembering to chain two general-purpose
ones every time.
"""

from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

register = template.Library()

# Unit codes (Product.UnitOfMeasure) that count discrete, indivisible
# items -- these always display as whole numbers ("54 pc", not
# "54.000 pc"). Everything else (KILOGRAM, GRAM, LITER, MILLILITER) is
# a continuous weight/volume measure and keeps two decimal places.
_WHOLE_NUMBER_UNITS = {"PIECE", "PACK", "BOX", "ROLL"}


@register.filter(name="peso")
@register.filter(name="comma2")
def peso(value):
    """Formats a number as currency text with a thousands separator
    and exactly two decimal places: 1210142.87 -> "1,210,142.87". Does
    NOT prepend the Php symbol -- every template already writes that
    as a literal &#8369; immediately before the value, and keeping the
    symbol out of the filter keeps it reusable for a plain number too
    (e.g. a CSV-style export with no currency symbol)."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    quantized = amount.quantize(Decimal("0.01"))
    return intcomma(quantized)


# `comma2` is the same formatting registered under a second name for a
# plain (non-currency) number that still wants a thousands separator
# and two decimal places -- e.g. Product Availability's "Total Stock"
# card, which sums quantities across mixed units and isn't itself a
# peso amount.


@register.filter(name="dadd")
def dadd(value, arg):
    """Decimal-safe addition for template use, in place of Django's
    built-in `add` filter -- `add` tries `int(value) + int(arg)`
    FIRST and only falls back to `value + arg` on a TypeError/
    ValueError, so two Decimals that both cast cleanly to int (e.g.
    1000000.00 and 210142.87) get truncated to whole pesos before
    being summed (1000000 + 210142 = 1210142, silently dropping the
    87 centavos). Found while adding the `peso` filter to the POS
    discount-total line (pos_ordering.html), which chains
    `|add:...|peso` -- `peso` was correctly formatting whatever
    number it was given, but `add` had already thrown away the
    cents. `dadd` just does `value + arg` directly, so Decimal
    arithmetic stays exact."""
    try:
        return Decimal(str(value)) + Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return value


@register.filter(name="qty")
def qty(value, unit_of_measure):
    """Formats a stored quantity (always a 3-decimal-place Decimal
    under the hood -- see Product.UnitOfMeasure's own docstring and
    InventoryItem.quantity_on_hand) according to what its unit
    actually means: a whole number for discrete, piece-counted units
    (pc/pack/box/roll), two decimal places for continuous weight/
    volume units (kg/g/L/mL) where a fractional amount is real and
    meaningful.

    `unit_of_measure` is the raw UnitOfMeasure value (e.g. "PIECE"),
    not its display label -- pass `product.unit_of_measure`, not
    `product.get_unit_of_measure_display`."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    if (unit_of_measure or "").upper() in _WHOLE_NUMBER_UNITS:
        return intcomma(amount.quantize(Decimal("1")))
    return intcomma(amount.quantize(Decimal("0.01")))
