"""
Tests for the cart views.

This module contains test cases for:
get_or_create_cart

"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from django.test import TestCase, Client, RequestFactory

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