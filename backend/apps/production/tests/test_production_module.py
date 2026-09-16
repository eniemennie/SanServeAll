"""
Tests for Week 8: Production Recording (Row 8.1), Ingredient Usage
Tracking (Row 8.2), and the Batch Management Interface (Row 8.3).
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Branch, Role, User
from apps.inventory.models import Inventory, InventoryTransaction, Product
from apps.production import services
from apps.production.models import ProductionRecord
from conftest import verify_otp_for_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def commissary():
    return Branch.objects.create(name="Commissary", code="COMMISSARY", is_commissary=True)


@pytest.fixture
def commissary_staff():
    role = Role.objects.create(name=Role.COMMISSARY_STAFF)
    return User.objects.create_user(username="commissary1", password="testpass123", role=role)


@pytest.fixture
def commissary_client(client, commissary_staff):
    client.force_login(commissary_staff)
    return client


@pytest.fixture
def flour(commissary):
    material = Product.objects.create(
        name="Flour (kg)", price="55.00", product_type=Product.ProductType.MATERIAL
    )
    Inventory.objects.create(branch=commissary, product=material, quantity_on_hand=50)
    return material


@pytest.fixture
def sugar(commissary):
    material = Product.objects.create(
        name="Sugar (kg)", price="60.00", product_type=Product.ProductType.MATERIAL
    )
    Inventory.objects.create(branch=commissary, product=material, quantity_on_hand=30)
    return material


@pytest.fixture
def cake():
    return Product.objects.create(
        name="Chocolate Cake", price="140.00", product_type=Product.ProductType.FINISHED_GOOD
    )


class TestRecordProduction:
    def test_valid_production_creates_a_record(
        self, commissary, commissary_staff, cake, flour, sugar
    ):
        record = services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[
                {"material_id": flour.pk, "quantity_used": 5},
                {"material_id": sugar.pk, "quantity_used": 3},
            ],
        )
        assert record.quantity_produced == 10
        assert record.ingredient_usages.count() == 2

    def test_production_deducts_material_stock(
        self, commissary, commissary_staff, cake, flour, sugar
    ):
        services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        flour_inventory = Inventory.objects.get(branch=commissary, product=flour)
        assert flour_inventory.quantity_on_hand == 45  # 50 - 5

    def test_production_credits_finished_good_stock(
        self, commissary, commissary_staff, cake, flour
    ):
        services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        cake_inventory = Inventory.objects.get(branch=commissary, product=cake)
        assert cake_inventory.quantity_on_hand == 10

    def test_logs_production_consumption_and_output_transactions(
        self, commissary, commissary_staff, cake, flour
    ):
        services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        consumption = InventoryTransaction.objects.get(
            branch=commissary,
            product=flour,
            movement_type=InventoryTransaction.MovementType.PRODUCTION_CONSUMPTION,
        )
        assert consumption.quantity_change == -5

        output = InventoryTransaction.objects.get(
            branch=commissary,
            product=cake,
            movement_type=InventoryTransaction.MovementType.PRODUCTION_OUTPUT,
        )
        assert output.quantity_change == 10

    def test_insufficient_material_stock_rejects_the_whole_run(
        self, commissary, commissary_staff, cake, flour, sugar
    ):
        """The core guarantee: if ANY material is insufficient, nothing
        gets deducted at all -- not even the materials that DID have
        enough stock."""
        with pytest.raises(services.ProductionError, match="Not enough"):
            services.record_production(
                commissary_staff=commissary_staff,
                product_id=cake.pk,
                quantity_produced=10,
                ingredient_rows=[
                    {"material_id": flour.pk, "quantity_used": 5},  # has 50, fine
                    {"material_id": sugar.pk, "quantity_used": 999},  # only has 30
                ],
            )

        flour_inventory = Inventory.objects.get(branch=commissary, product=flour)
        assert flour_inventory.quantity_on_hand == 50  # untouched, not partially deducted
        assert not ProductionRecord.objects.exists()

    def test_zero_quantity_produced_is_rejected(self, commissary, commissary_staff, cake, flour):
        with pytest.raises(services.ProductionError, match="greater than zero"):
            services.record_production(
                commissary_staff=commissary_staff,
                product_id=cake.pk,
                quantity_produced=0,
                ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
            )

    def test_no_ingredients_is_rejected(self, commissary, commissary_staff, cake):
        with pytest.raises(services.ProductionError, match="At least one ingredient"):
            services.record_production(
                commissary_staff=commissary_staff,
                product_id=cake.pk,
                quantity_produced=10,
                ingredient_rows=[],
            )

    def test_no_commissary_configured_raises_clear_error(self):
        """Tested in isolation -- no commissary, no materials, no
        Inventory rows at all -- since Inventory.branch uses PROTECT and
        correctly refuses to let an already-referenced commissary branch
        be deleted once fixtures like `flour` have created stock there."""
        role = Role.objects.create(name=Role.COMMISSARY_STAFF)
        staff = User.objects.create_user(
            username="isolated_staff", password="testpass123", role=role
        )
        product = Product.objects.create(name="Isolated Cake", price="100.00")

        with pytest.raises(services.ProductionError, match="No commissary branch"):
            services.record_production(
                commissary_staff=staff,
                product_id=product.pk,
                quantity_produced=10,
                ingredient_rows=[{"material_id": 1, "quantity_used": 5}],
            )


class TestRecordProductionView:
    def test_commissary_staff_can_access_the_form(self, commissary_client):
        response = commissary_client.get(reverse("production:record"))
        assert response.status_code == 200

    def test_branch_staff_cannot_access_the_form(self, client):
        role = Role.objects.create(name=Role.BRANCH_STAFF)
        staff = User.objects.create_user(username="cashier1", password="testpass123", role=role)
        client.force_login(staff)
        response = client.get(reverse("production:record"))
        assert response.status_code == 403

    def test_valid_submission_creates_a_record_and_redirects(
        self, commissary_client, commissary, cake, flour
    ):
        response = commissary_client.post(
            reverse("production:record"),
            {
                "product_id": cake.pk,
                "quantity_produced": "10",
                "material_id": [str(flour.pk)],
                "quantity_used": ["5"],
                "quality": "PASS",
                "status": "COMPLETED",
            },
        )
        assert response.status_code == 302
        assert ProductionRecord.objects.count() == 1


class TestBatchManagementView:
    def test_commissary_staff_can_view_batches(self, commissary_client):
        response = commissary_client.get(reverse("production:batch_management"))
        assert response.status_code == 200

    def test_owner_can_view_batches(self, client):
        role = Role.objects.create(name=Role.OWNER_ADMIN)
        owner = User.objects.create_user(username="owner1", password="testpass123", role=role)
        client.force_login(owner)
        verify_otp_for_client(client, owner)
        response = client.get(reverse("production:batch_management"))
        assert response.status_code == 200

    def test_delete_removes_the_record(self, commissary_client, commissary_staff, cake, flour):
        record = services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        response = commissary_client.post(reverse("production:delete_record", args=[record.pk]))
        assert response.status_code == 302
        assert not ProductionRecord.objects.filter(pk=record.pk).exists()

    def test_delete_does_not_reverse_inventory_changes(
        self, commissary_client, commissary, commissary_staff, cake, flour
    ):
        """Deliberate design choice: deleting the audit record doesn't
        silently undo the stock effects it caused."""
        record = services.record_production(
            commissary_staff=commissary_staff,
            product_id=cake.pk,
            quantity_produced=10,
            ingredient_rows=[{"material_id": flour.pk, "quantity_used": 5}],
        )
        commissary_client.post(reverse("production:delete_record", args=[record.pk]))

        flour_inventory = Inventory.objects.get(branch=commissary, product=flour)
        assert flour_inventory.quantity_on_hand == 45  # still deducted, not restored
