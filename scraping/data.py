"""
scraping.data — re-export shim.

The canonical implementation lives in ``wuwa_inventory_kamera.scraping.data``.
This shim keeps legacy ``from scraping.data import ...`` imports working.
"""
from wuwa_inventory_kamera.scraping.data import (  # noqa: F401
    itemsID,
    charactersID,
    weaponsID,
    echoesID,
    achievementsID,
    echoStats,
    definedText,
    sonataName,
    loadData,
)
