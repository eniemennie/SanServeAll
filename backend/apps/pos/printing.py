"""
Bluetooth Receipt Printer Integration (Row 12.3).

Real-world constraint: browsers have no reliable, broadly-compatible way
to talk directly to a Bluetooth ESC/POS thermal printer (Web Bluetooth
exists, but thermal receipt printers rarely expose GATT services it can
use). The pragmatic, actually-workable answer -- confirmed via real
research into how other web-based POS systems solve this exact problem,
not guessed at -- is RawBT: a free Android app that already handles the
Bluetooth/USB/Wi-Fi connection to the physical printer, and accepts a
print job from any browser via a custom URL scheme. A web page never
touches Bluetooth directly; it hands RawBT a ready-to-print ESC/POS byte
stream and RawBT does the rest.

RawBT's real protocol (verified against escpos-php's own actively-
maintained RawbtPrintConnector, not an assumption):
    intent:base64,<BASE64_ESCPOS_BYTES>#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;

This is a genuine Android intent URL (see developer.chrome.com/docs/android/intents)
-- tapping it hands the print job to RawBT if installed, or Chrome falls
back to the Play Store page if not.
"""

import base64

RAWBT_PACKAGE = "ru.a402d.rawbtprinter"
RECEIPT_WIDTH_CHARS = 32  # standard for common 58mm thermal receipt printers

# ESC/POS control codes -- see the ESC/POS Programming Manual (Epson).
_INIT = b"\x1b\x40"  # ESC @ : reset printer to defaults
_ALIGN_LEFT = b"\x1b\x61\x00"
_ALIGN_CENTER = b"\x1b\x61\x01"
_BOLD_ON = b"\x1b\x45\x01"
_BOLD_OFF = b"\x1b\x45\x00"
_DOUBLE_SIZE_ON = b"\x1d\x21\x11"
_DOUBLE_SIZE_OFF = b"\x1d\x21\x00"
_CUT = b"\x1d\x56\x00"  # full cut
_FEED_LINES = b"\n\n\n"


def _ascii_currency(symbol):
    """Thermal printers use their own single-byte codepage (commonly
    CP437/CP858), not UTF-8 -- correctly switching codepages per printer
    model is genuinely printer-hardware-specific and can't be verified
    without real hardware to test against. Rather than guess at that, the
    printed receipt uses a safe ASCII-only stand-in for the currency
    symbol; the on-screen HTML receipt still shows the real character
    (Php/peso sign), since browsers render UTF-8 correctly regardless."""
    return {"\u20b1": "P"}.get(symbol, symbol)


def _safe_ascii(text):
    """Encodes to ASCII, substituting anything else with '?' -- the
    printed receipt must never crash on unexpected characters (a
    business name, address, or footer text an Owner/Admin typed into
    System Settings could contain anything)."""
    return text.encode("ascii", errors="replace")


def _line(left, right="", width=RECEIPT_WIDTH_CHARS):
    """Right-justifies `right` against `left` within `width` columns --
    the classic "item name .... price" receipt line layout, using plain
    spaces since thermal printers use a monospace font by default."""
    if not right:
        return left[:width]
    space = max(1, width - len(left) - len(right))
    return f"{left}{' ' * space}{right}"[:width]


# Two fixed lines every printed receipt must carry, regardless of the
# business's own configurable BusinessSettings.receipt_footer_text
# (task tracker, 2026-11-08: "match the digital receipt" -- required
# footer). The first one happens to be BusinessSettings.receipt_footer_text's
# own default value, so a business left on that default would otherwise see
# "Thank you for your purchase!" printed twice in a row; _required_footer_lines()
# below skips the configurable line in that case rather than duplicate it.
REQUIRED_FOOTER_LINES = (
    "Thank you for your purchase!",
    "This is just a supplementary receipt.",
)


def _required_footer_lines(configured_footer_text):
    """Returns the footer lines to print, in order: the business's own
    configured message first (if any, and only when it isn't already
    identical to the first required line), then the two lines every
    receipt must carry. Never recalculates or alters either value --
    just decides whether the configured one is redundant."""
    configured = (configured_footer_text or "").strip()
    lines = []
    if configured and configured != REQUIRED_FOOTER_LINES[0]:
        lines.append(configured)
    lines.extend(REQUIRED_FOOTER_LINES)
    return lines


def build_escpos_receipt(transaction):
    """Builds the raw ESC/POS byte sequence for a completed
    SalesTransaction -- the same information shown on receipt.html
    (business info, branch, cashier, items, discount, VAT breakdown,
    total, payment, change), formatted for a physical thermal printer
    instead of a browser, plus the two required footer lines every
    printed receipt must carry (task tracker, 2026-11-08: "match the
    digital receipt"). All financial figures below are read straight
    from the transaction's own model properties/methods -- the same
    ones receipt.html uses -- never recalculated here."""
    from apps.system_config.models import BusinessSettings

    settings_obj = BusinessSettings.load()
    currency = _ascii_currency(settings_obj.currency_symbol)

    lines = [_INIT, _ALIGN_CENTER, _BOLD_ON, _DOUBLE_SIZE_ON]
    lines.append(_safe_ascii(settings_obj.business_name) + b"\n")
    lines.append(_DOUBLE_SIZE_OFF + _BOLD_OFF)

    if settings_obj.business_address:
        lines.append(_safe_ascii(settings_obj.business_address) + b"\n")

    lines.append(_safe_ascii(f"Transaction #{transaction.pk}") + b"\n")
    lines.append(_safe_ascii(f"{transaction.completed_at:%Y-%m-%d %H:%M}") + b"\n")
    lines.append(_safe_ascii(transaction.branch.name) + b"\n")
    cashier_name = transaction.cashier.get_full_name() or transaction.cashier.username
    lines.append(_safe_ascii(f"Cashier: {cashier_name}") + b"\n")
    lines.append(_safe_ascii("-" * RECEIPT_WIDTH_CHARS) + b"\n")

    lines.append(_ALIGN_LEFT)
    for item in transaction.items.all():
        name = f"{item.display_name} x{item.quantity}"
        price = f"{currency}{item.subtotal}"
        lines.append(_safe_ascii(_line(name, price)) + b"\n")

    lines.append(_safe_ascii("-" * RECEIPT_WIDTH_CHARS) + b"\n")
    lines.append(_safe_ascii(_line("Subtotal", f"{currency}{transaction.total_amount}")) + b"\n")

    # Discount and VAT breakdown: identical formulas/model calls to the
    # ones apps/pos/views.py's receipt() view passes to receipt.html, so
    # the printed figures can never drift from the digital ones.
    discount_total = transaction.item_discounts_total + transaction.transaction_discount_value
    if discount_total:
        lines.append(_safe_ascii(_line("Discount", f"-{currency}{discount_total}")) + b"\n")
    vat_exclusive, vat_amount = transaction.vat_breakdown(settings_obj.tax_rate_percent)
    if vat_amount:
        lines.append(_safe_ascii(_line("VATable Sales", f"{currency}{vat_exclusive}")) + b"\n")
        tax_rate_label = f"VAT ({settings_obj.tax_rate_percent:.0f}%)"
        lines.append(_safe_ascii(_line(tax_rate_label, f"{currency}{vat_amount}")) + b"\n")
    else:
        lines.append(_safe_ascii(_line("VAT-Exempt Sales", f"{currency}{vat_exclusive}")) + b"\n")

    lines.append(_BOLD_ON)
    lines.append(_safe_ascii(_line("TOTAL", f"{currency}{transaction.grand_total}")) + b"\n")
    lines.append(_BOLD_OFF)
    lines.append(_safe_ascii(_line(transaction.get_payment_method_display(), "")) + b"\n")
    lines.append(_safe_ascii(_line("Paid", f"{currency}{transaction.amount_tendered}")) + b"\n")
    lines.append(_safe_ascii(_line("Change", f"{currency}{transaction.change_due}")) + b"\n")

    lines.append(_ALIGN_CENTER)
    footer_lines = _required_footer_lines(settings_obj.receipt_footer_text)
    lines.append(_safe_ascii("\n" + "\n".join(footer_lines)) + b"\n")

    lines.append(_FEED_LINES)
    lines.append(_CUT)

    return b"".join(lines)


def build_rawbt_print_url(transaction):
    """Returns the full RawBT intent URL for one transaction's receipt,
    ready to use as an <a href="..."> link -- tapping it hands the print
    job straight to RawBT (or Chrome's Play Store fallback if the app
    isn't installed yet)."""
    escpos_bytes = build_escpos_receipt(transaction)
    encoded = base64.b64encode(escpos_bytes).decode("ascii")
    return f"intent:base64,{encoded}#Intent;scheme=rawbt;package={RAWBT_PACKAGE};end;"
