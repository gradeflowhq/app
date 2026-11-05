from django.dispatch import Signal

key_pair_replaced = Signal()
"""Sent with `user` when a password reset gives them a new key pair."""
