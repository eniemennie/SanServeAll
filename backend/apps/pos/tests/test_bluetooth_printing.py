"""
Tests for Week 12 Row 12.3: Bluetooth Receipt Printer Integration.
Verifies the ESC/POS byte generation and the RawBT intent URL format
against the real, verified protocol (see printing.py's module docstring
for the source), not an assumed one.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Product
from apps.pos import printing
from apps.pos.models import SalesItem, SalesTransaction
from apps.system_config.models import BusinessSettings

pytestmark = pytest.mark.django_db


@pytest.fixture
def branch():
    return Branch.objects.create(name="Lipa City", code="LIPA")


@pytest.fixture
def cashier(branch):
    role = Role.objects.create(name=Role.BRANCH_STAFF)
    return User.objects.create_user(
        username="cashier1", password="testpass123", role=role, branch=branch
    )


@pytest.fixture
def completed_transaction(branch, cashier):
    product = Product.objects.create(name="Spanish Latte", price="125.00")
    transaction = SalesTransaction.objects.create(
        branch=branch,
        cashier=cashier,
        status=SalesTransaction.Status.COMPLETED,
        payment_method="CASH",
        amount_tendered=Decimal("500.00"),
        completed_at=timezone.now(),
    )
    SalesItem.objects.create(
        transaction=transaction, product=product, unit_price=Decimal("125.00"), quantity=2
    )
    return transaction


class TestBuildEscposReceipt:
    def test_returns_bytes_starting_with_the_init_command(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert isinstance(receipt_bytes, bytes)
        assert receipt_bytes.startswith(b"\x1b\x40")  # ESC @ : initialize

    def test_ends_with_feed_and_cut_command(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert receipt_bytes.endswith(b"\x1d\x56\x00")  # GS V 0 : full cut

    def test_contains_the_business_name(self, completed_transaction):
        BusinessSettings.objects.filter(pk=1).delete()
        settings_obj = BusinessSettings.load()
        settings_obj.business_name = "Jorge's Test Cafe"
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Jorge's Test Cafe" in receipt_bytes

    def test_contains_the_product_name_and_quantity(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Spanish Latte" in receipt_bytes
        assert b"x2" in receipt_bytes

    def test_never_crashes_on_the_peso_sign_currency_symbol(self, completed_transaction):
        """The critical fix: a naive .encode('ascii') on any line
        containing the (non-ASCII) peso sign would raise
        UnicodeEncodeError. This must never happen, regardless of what
        currency symbol is configured."""
        settings_obj = BusinessSettings.load()
        settings_obj.currency_symbol = "\u20b1"  # actual peso sign
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert isinstance(receipt_bytes, bytes)  # didn't raise
        assert b"P250" in receipt_bytes or b"P125" in receipt_bytes  # ASCII fallback used

    def test_never_crashes_on_unusual_business_name_characters(self, completed_transaction):
        """A business name/address/footer typed into System Settings
        could contain anything -- must never crash the receipt."""
        settings_obj = BusinessSettings.load()
        settings_obj.business_name = "Café Ñoño 日本語"
        settings_obj.receipt_footer_text = "Merci! ありがとう"
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert isinstance(receipt_bytes, bytes)  # didn't raise

    def test_includes_total_and_change_due(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"TOTAL" in receipt_bytes
        assert b"250.00" in receipt_bytes  # 2 x 125.00
        assert b"250.00" in receipt_bytes  # change: 500 tendered - 250 total

    def test_still_produces_a_valid_receipt_when_footer_setting_is_blank(
        self, completed_transaction
    ):
        settings_obj = BusinessSettings.load()
        settings_obj.receipt_footer_text = ""
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        # Should not crash and should still produce a valid receipt
        assert receipt_bytes.startswith(b"\x1b\x40")


class TestPrintedReceiptMatchesDigitalReceipt:
    """Task tracker, 2026-11-08: "match the digital receipt" -- the
    printed/thermal receipt must show the same branch, cashier,
    discount, and VAT breakdown information as receipt.html, computed
    from the exact same SalesTransaction properties/methods (never
    recalculated independently), plus two fixed footer lines that must
    always be present regardless of the BusinessSettings.receipt_footer_text
    setting."""

    def test_includes_the_branch_name(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Lipa City" in receipt_bytes

    def test_includes_the_cashier_name(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Cashier: cashier1" in receipt_bytes

    def test_includes_the_cashiers_full_name_when_set(self, completed_transaction):
        completed_transaction.cashier.first_name = "Juana"
        completed_transaction.cashier.last_name = "Dela Cruz"
        completed_transaction.cashier.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Cashier: Juana Dela Cruz" in receipt_bytes

    def test_includes_a_discount_line_matching_the_transactions_discount_value(
        self, completed_transaction
    ):
        from apps.pos.models import DiscountCategory, DiscountType

        completed_transaction.transaction_discount_category = DiscountCategory.SC2
        completed_transaction.transaction_discount_type = DiscountType.PERCENT
        completed_transaction.transaction_discount_amount = Decimal("20")
        completed_transaction.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        expected_discount = (
            completed_transaction.item_discounts_total
            + completed_transaction.transaction_discount_value
        )
        assert b"Discount" in receipt_bytes
        assert _amount_bytes(expected_discount) in receipt_bytes

    def test_omits_the_discount_line_when_there_is_no_discount(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Discount" not in receipt_bytes

    def test_includes_a_vat_breakdown_matching_the_transactions_vat_breakdown(
        self, completed_transaction
    ):
        settings_obj = BusinessSettings.load()
        vat_exclusive, vat_amount = completed_transaction.vat_breakdown(
            settings_obj.tax_rate_percent
        )

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"VAT" in receipt_bytes
        assert _amount_bytes(vat_exclusive) in receipt_bytes
        assert _amount_bytes(vat_amount) in receipt_bytes

    def test_shows_vat_exempt_sales_for_an_exempt_discount_category(self, completed_transaction):
        from apps.pos.models import DiscountCategory, DiscountType

        completed_transaction.transaction_discount_category = DiscountCategory.SC2
        completed_transaction.transaction_discount_type = DiscountType.PERCENT
        completed_transaction.transaction_discount_amount = Decimal("20")
        completed_transaction.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"VAT-Exempt Sales" in receipt_bytes

    def test_always_includes_both_required_footer_lines(self, completed_transaction):
        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Thank you for your purchase!" in receipt_bytes
        assert b"This is just a supplementary receipt." in receipt_bytes

    def test_required_footer_lines_present_even_when_footer_setting_is_blanked(
        self, completed_transaction
    ):
        settings_obj = BusinessSettings.load()
        settings_obj.receipt_footer_text = ""
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Thank you for your purchase!" in receipt_bytes
        assert b"This is just a supplementary receipt." in receipt_bytes

    def test_does_not_duplicate_the_default_footer_text(self, completed_transaction):
        """BusinessSettings.receipt_footer_text defaults to the exact same
        text as the first required footer line -- left at that default,
        the receipt must show it once, not twice in a row."""
        settings_obj = BusinessSettings.load()
        settings_obj.receipt_footer_text = "Thank you for your purchase!"
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert receipt_bytes.count(b"Thank you for your purchase!") == 1

    def test_keeps_a_distinct_configured_footer_message_alongside_the_required_lines(
        self, completed_transaction
    ):
        settings_obj = BusinessSettings.load()
        settings_obj.receipt_footer_text = "Follow us on Instagram @jorgescafe!"
        settings_obj.save()

        receipt_bytes = printing.build_escpos_receipt(completed_transaction)
        assert b"Follow us on Instagram @jorgescafe!" in receipt_bytes
        assert b"Thank you for your purchase!" in receipt_bytes
        assert b"This is just a supplementary receipt." in receipt_bytes

    def test_long_product_name_and_multiple_items_do_not_break_the_layout(self, branch, cashier):
        """A product name long enough to collide with its price column,
        and more than one line item, must never corrupt the byte stream
        or crash -- _line()'s truncation must hold under real-world data."""
        product = Product.objects.create(
            name="Iced Caramel Macchiato with Extra Espresso Shot and Oat Milk",
            price="180.00",
        )
        transaction = SalesTransaction.objects.create(
            branch=branch,
            cashier=cashier,
            status=SalesTransaction.Status.COMPLETED,
            payment_method="CASH",
            amount_tendered=Decimal("1000.00"),
            completed_at=timezone.now(),
        )
        SalesItem.objects.create(
            transaction=transaction, product=product, unit_price=Decimal("180.00"), quantity=3
        )
        SalesItem.objects.create(
            transaction=transaction,
            custom_name="Croissant",
            unit_price=Decimal("85.00"),
            quantity=2,
        )

        receipt_bytes = printing.build_escpos_receipt(transaction)
        assert isinstance(receipt_bytes, bytes)
        assert receipt_bytes.startswith(b"\x1b\x40")
        assert receipt_bytes.endswith(b"\x1d\x56\x00")
        assert b"Thank you for your purchase!" in receipt_bytes
        assert b"This is just a supplementary receipt." in receipt_bytes
        assert b"Croissant" in receipt_bytes

        # printing._line() is what actually enforces the paper width for
        # every item/discount/VAT/total line -- confirm directly that a
        # long product name doesn't defeat its truncation, rather than
        # trying to recover each line's width from the full byte stream
        # (which also carries non-printing ESC/POS control bytes that
        # have nothing to do with the paper's character width).
        long_name_line = printing._line(
            "Iced Caramel Macchiato with Extra Espresso Shot and Oat Milk x3",
            "540.00",
        )
        assert len(long_name_line) <= printing.RECEIPT_WIDTH_CHARS

    def test_receipt_and_direct_bluetooth_payload_byte_streams_are_identical(
        self, completed_transaction
    ):
        """Both print paths (RawBT intent URL and the direct Web
        Bluetooth JSON payload) must build the receipt via the same
        function, so neither path can ever produce a different receipt
        or trigger a second, separate build -- no duplicate print jobs
        from one transaction."""
        import base64
        import re

        direct_payload = printing.build_escpos_receipt(completed_transaction)
        rawbt_url = printing.build_rawbt_print_url(completed_transaction)
        match = re.search(r"intent:base64,([^#]+)#", rawbt_url)
        rawbt_payload = base64.b64decode(match.group(1))

        assert direct_payload == rawbt_payload


def _amount_bytes(decimal_amount):
    """ASCII-encodes a Decimal the same way printing.py's own f-strings
    do (plain str(Decimal), no manual quantization), so test
    expectations never drift from the real formatting."""
    return str(decimal_amount).encode("ascii")


class TestBuildRawbtPrintUrl:
    def test_url_uses_the_correct_rawbt_scheme_and_package(self, completed_transaction):
        url = printing.build_rawbt_print_url(completed_transaction)
        assert url.startswith("intent:base64,")
        assert "scheme=rawbt" in url
        assert "package=ru.a402d.rawbtprinter" in url
        assert url.endswith("end;")

    def test_base64_payload_decodes_back_to_valid_escpos_bytes(self, completed_transaction):
        import base64
        import re

        url = printing.build_rawbt_print_url(completed_transaction)
        match = re.search(r"intent:base64,([^#]+)#", url)
        encoded_payload = match.group(1)

        decoded = base64.b64decode(encoded_payload)
        assert decoded == printing.build_escpos_receipt(completed_transaction)
        assert decoded.startswith(b"\x1b\x40")


class TestReceiptViewIncludesRawbtUrl:
    def test_receipt_page_includes_the_rawbt_print_button(
        self, client, cashier, branch, completed_transaction
    ):
        from django.urls import reverse

        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        session["pos_unlocked"] = True
        session.save()

        response = client.get(reverse("pos:receipt", args=[completed_transaction.pk]))
        assert response.status_code == 200
        assert b"Print via Bluetooth Printer" in response.content
        assert b"intent:base64," in response.content


class TestReceiptPrintFontSize:
    """Task #145 (client review): the printed/RawBT-thermal receipt was
    too small to read. receipt.html had no explicit font-size styling
    at all, relying entirely on Bootstrap's defaults (including the
    0.875em `.small`/`.text-muted small` classes used on most line
    items). A @media print block now bumps those sizes back up,
    scoped to print only so the on-screen view is unaffected."""

    def test_receipt_page_has_a_print_only_font_size_boost(
        self, client, cashier, branch, completed_transaction
    ):
        from django.urls import reverse

        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        session["pos_unlocked"] = True
        session.save()

        response = client.get(reverse("pos:receipt", args=[completed_transaction.pk]))
        content = response.content.decode()

        assert "@media print" in content
        # Scoped to print, not applied unconditionally to the page --
        # the raw font-size override must appear inside the @media
        # print block, not as a bare top-level rule.
        print_block = content.split("@media print", 1)[1]
        assert "font-size: 1.15rem" in print_block
        assert "font-size: 1rem" in print_block

    def test_receipt_still_renders_correctly_with_the_new_styling(
        self, client, cashier, branch, completed_transaction
    ):
        """The styling change must not disturb the actual receipt
        content/data -- transaction info, items, and totals still
        render exactly as before."""
        from django.urls import reverse

        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        session["pos_unlocked"] = True
        session.save()

        response = client.get(reverse("pos:receipt", args=[completed_transaction.pk]))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Spanish Latte" in content
        assert f"Transaction #{completed_transaction.pk}" in content


class TestReceiptEscposPayloadView:
    """Direct-to-laptop Web Bluetooth print path (task tracker,
    2026-11-08) -- the JSON endpoint static/js/escpos_bluetooth_print.js
    fetches before writing bytes straight to the printer's BLE
    characteristic. Same data as the RawBT intent URL, just not wrapped
    in RawBT's intent:// scheme, and same ownership/branch scoping as
    the existing receipt view it sits beside."""

    def _unlock(self, client, cashier, branch):
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        session["pos_unlocked"] = True
        session.save()

    def test_returns_base64_escpos_matching_build_escpos_receipt(
        self, client, cashier, branch, completed_transaction
    ):
        import base64

        from django.urls import reverse

        self._unlock(client, cashier, branch)
        response = client.get(
            reverse("pos:receipt_escpos_payload", args=[completed_transaction.pk])
        )

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        payload = response.json()
        decoded = base64.b64decode(payload["escpos_base64"])
        assert decoded == printing.build_escpos_receipt(completed_transaction)
        assert decoded.startswith(b"\x1b\x40")

    def test_404s_for_a_transaction_belonging_to_another_cashier(
        self, client, branch, completed_transaction
    ):
        from django.urls import reverse

        other_cashier = User.objects.create_user(
            username="cashier2",
            password="testpass123",
            role=completed_transaction.cashier.role,
            branch=branch,
        )
        self._unlock(client, other_cashier, branch)

        response = client.get(
            reverse("pos:receipt_escpos_payload", args=[completed_transaction.pk])
        )
        assert response.status_code == 404

    def test_denies_access_when_selected_branch_does_not_match_the_transactions_branch(
        self, client, cashier, completed_transaction
    ):
        """A cashier whose session branch doesn't match the transaction's
        branch never reaches the view's own 404 check -- pos_unlock_required
        itself redirects first (a cashier is tied to one branch, so a
        mismatched selected_branch_id means their session is stale/invalid).
        Either way, the payload must not be served."""
        from django.urls import reverse

        other_branch = Branch.objects.create(name="Batangas City", code="BATC")
        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = other_branch.pk
        session["pos_unlocked"] = True
        session.save()

        response = client.get(
            reverse("pos:receipt_escpos_payload", args=[completed_transaction.pk])
        )
        assert response.status_code != 200

    def test_404s_for_a_non_completed_transaction(self, client, branch, cashier):
        from django.urls import reverse

        draft = SalesTransaction.objects.create(
            branch=branch, cashier=cashier, status=SalesTransaction.Status.DRAFT
        )
        self._unlock(client, cashier, branch)

        response = client.get(reverse("pos:receipt_escpos_payload", args=[draft.pk]))
        assert response.status_code == 404

    def test_requires_pos_unlock(self, client, cashier, branch, completed_transaction):
        from django.urls import reverse

        client.force_login(cashier)
        session = client.session
        session["selected_branch_id"] = branch.pk
        # Deliberately not setting pos_unlocked=True.
        session.save()

        response = client.get(
            reverse("pos:receipt_escpos_payload", args=[completed_transaction.pk])
        )
        assert response.status_code != 200
