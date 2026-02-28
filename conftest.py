"""
Root conftest.py — puts the project root on sys.path so that package imports
like ``from scraping.processing.echoesValidator import ...`` work when pytest
is invoked from any directory.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
