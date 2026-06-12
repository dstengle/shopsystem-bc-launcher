"""
pytest-bdd test module for product-slug-derived BC-name validation scenarios.

Step definitions live in conftest.py.
"""
from pytest_bdd import scenarios

scenarios("../features/bc_manifest_product_slug.feature")
