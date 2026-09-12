"""
Tests for the cart views.

This module contains test cases for:
get_or_create_cart
cart_detail
add_to_cart

"""

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.utils import IntegrityError
from django.test import TestCase, RequestFactory
from django.urls import reverse

from cart.models import Cart, CartItem, Wishlist
from cart.views import get_or_create_cart

User = get_user_model()


class GetOrCreateCartTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            email="test@example.com",
        )
        self.factory = RequestFactory()

    def test_creates_cart_for_authenticated_user_with_no_cart(self):
        self.assertFalse(Cart.objects.filter(user=self.user).exists())

        request = self.factory.get("/")
        request.user = self.user
        cart = get_or_create_cart(request)

        self.assertIsNotNone(cart)
        self.assertEqual(cart.user, self.user)
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_returns_existing_cart_for_authenticated_user(self):
        """
        edge: making a cart for someone who already has it, max 1 cart should exist
        """
        existing_cart = Cart.objects.create(user=self.user)

        request = self.factory.get("/")
        request.user = self.user
        cart = get_or_create_cart(request)

        self.assertEqual(cart, existing_cart)
        self.assertEqual(Cart.objects.filter(user=self.user).count(), 1)

    def test_returns_none_for_anonymous_user(self):
        request = self.factory.get("/")
        request.user = AnonymousUser()
        cart = get_or_create_cart(request)

        self.assertIsNone(cart)


class CartDetailTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            email="test@example.com",
        )
        self.client.login(username="testuser", password="testpass123")

    def test_auth_user_no_cart_detail(self):
        self.assertFalse(Cart.objects.filter(user=self.user).exists())
        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_price"], 0)
        self.assertEqual(response.context["total_items"], 0)
        self.assertIsNotNone(response.context["cart"])
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_auth_user_with_empty_cart_detail(self):
        existing_cart = Cart.objects.create(user=self.user)
        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_price"], 0)
        self.assertEqual(response.context["total_items"], 0)
        self.assertIsNotNone(response.context["cart"])
        self.assertTrue(Cart.objects.filter(user=self.user).exists())

    def test_auth_user_with_nonempty_cart_detail(self):
        existing_cart = Cart.objects.create(user=self.user)
        existing_cart.add_item(product_id="test-product", quantity=2, price=Decimal("10.00"))

        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cart"], existing_cart)
        self.assertEqual(response.context["total_items"], 2)
        self.assertEqual(response.context["total_price"], Decimal("20.00"))
        self.assertEqual(Cart.objects.filter(user=self.user).count(), 1)

    def test_anonymous_user_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(reverse("cart_detail"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))
        self.assertIn("next=", response.url)


class AddToCartTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            email="test@example.com",
        )
        self.client.login(username="testuser", password="testpass123")

# product_id - CharField
    def test_product_id_exceeds_max_length(self):
        """
        BUG: view never validates product_id length before saving.
        Should be rejected with 400;
        currently succeeds (silently truncated on SQLLite)
        or crashing (in other DB like PGSQL)

        PROPOSING FIX:
        add validator in the view
        """
        long_id = "x" * 500  # max_length=255
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": long_id, "quantity": 1, "price": "10.00",
        })
        self.assertEqual(response.status_code, 200)

# quantity - PositiveIntegerField
    def test_quantity_negative(self):
        """
        BUG: PositiveIntegerField's CHECK constraint blocks this at the DB,
        but the view doesn't catch it, so it crashes instead of 400

        PROPOSING FIX:
        add validator in the view
        """
        with self.assertRaises(IntegrityError):
            self.client.post(reverse("add_to_cart"), {
                "product_id": "test-product", "quantity": -5, "price": "10.00",
            })

    def test_quantity_zero(self):
        """
        BUG: 0 passes PositiveIntegerField's >= 0 check, a CartItem row
        IS created with quantity=0 instead of being a no-op

        PROPOSING FIX:
        add validator in the view, reject quantity <= 0
        """
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 0, "price": "10.00",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIsNotNone(data["item_id"])  # a row was created despite qty=0
        self.assertEqual(data["total_items"], 0)
        self.assertEqual(Cart.objects.get(id=response.wsgi_request.user.cart.id).items.count(), 1)

    def test_quantity_zero_then_real_quantity_merges_into_same_row(self):
        """
        BUG: consequence of test_quantity_zero — add_item's existing-item
        lookup filters on (product_id, extra_data) only, not quantity, so
        the zero-row gets found and incremented by a later real add
        instead of the zero-row never existing in the first place

        PROPOSING FIX:
        same as test_quantity_zero — reject quantity <= 0 in the view
        """
        first = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 0, "price": "10.00",
        })
        self.assertEqual(first.status_code, 200)
        first_item_id = first.json()["item_id"]

        second = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 5, "price": "10.00",
        })
        self.assertEqual(second.status_code, 200)
        data = second.json()

        # Same row reused, not a new one
        self.assertEqual(data["item_id"], first_item_id)
        self.assertEqual(data["total_items"], 5)

        cart = Cart.objects.get(user=self.user)
        self.assertEqual(cart.items.count(), 1)
        self.assertEqual(cart.items.get(id=first_item_id).quantity, 5)

    def test_quantity_extremely_large(self):
        """
        BUG: no upper bound on quantity anywhere — not in the view, not
        on the model field, not at the DB level (1 billion is still
        under Postgres's ~2.1B int ceiling)

        PROPOSING FIX:
        add a sane max quantity check in the view
        """
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 10**9, "price": "10.00",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_items"], 10**9)

# price - DecimalField
    def test_price_negative(self):
        """
        BUG: no sign check anywhere, silently succeeds with a negative price

        PROPOSING FIX:
        add validator in the view, reject price < 0
        """
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 1, "price": "-10.00",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_price"], "-10.00")

    def test_price_zero(self):
        """
        Free item 0.00 price
        """
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 1, "price": "0.00",
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["total_price"], "0.00")

    def test_price_exceeds_max_digits(self):
        """
        BUG: SQLite stores an oversized price fine on write, it blows up
        later on Decimal read instead of failing cleanly at input time

        PROPOSING FIX:
        add validator in the view, check digit count before saving
        """
        with self.assertRaises(InvalidOperation):
            self.client.post(reverse("add_to_cart"), {
                "product_id": "test-product", "quantity": 1, "price": "99999999999.99",
            })

    def test_anonymous_user_redirected_to_login(self):
        self.client.logout()
        response = self.client.post(reverse("add_to_cart"), {
            "product_id": "test-product", "quantity": 1, "price": "10.00",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))

    def test_get_request_rejected(self):
        """
        require_POST should 405 a GET without running the view body
        """
        response = self.client.get(reverse("add_to_cart"))
        self.assertEqual(response.status_code, 405)