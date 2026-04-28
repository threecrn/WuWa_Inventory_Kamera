"""
wuwa_inventory_kamera.updater.assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Core asset downloader — no GUI/Qt dependencies.

Downloads icon PNGs from the upstream icon repository into ``assets/``.
The Qt-dependent ``AssetsUpdater`` (with Signal support) remains in
``updater/assetsUpdater.py`` and subclasses this.
"""
from __future__ import annotations

import json
import urllib.request
import logging
from pathlib import Path
from dataclasses import dataclass

from ..config.app_config import basePATH

logger = logging.getLogger('AssetsUpdater')


@dataclass
class PathConfig:
    folder: list[str]
    sub: list[str]


class BaseAssetsUpdater:
    """Qt-free asset downloader.

    Subclass and override :meth:`_onProgress` / :meth:`_onFinished` for
    lifecycle notifications (e.g. Qt signals).
    """

    API = 'https://api.github.com/repos/{owner}/{repo}/contents/{path}'

    def __init__(self) -> None:
        self.author = 'Stormy-Waves'
        self.repo = 'WW_Icon'
        self.pathConfig = PathConfig(
            ['UIResources', 'Common', 'Image'],
            ['IconA', 'IconC', 'IconCook', 'IconMout', 'IconMst', 'IconRup', 'IconTask', 'IconWup'],
        )

    def makeFolder(self, filePath: Path) -> None:
        try:
            filePath.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create folder: %s", e, exc_info=True)

    def fetchFileData(self, url: str) -> dict | list:
        try:
            with urllib.request.urlopen(urllib.request.Request(url)) as response:
                return json.loads(response.read().decode())
        except Exception:
            return {}

    def run(self) -> None:
        baseUrl = self.API.format(
            owner=self.author,
            repo=self.repo,
            path='/'.join(self.pathConfig.folder),
        )

        for folder in self.pathConfig.sub:
            path: Path = basePATH / 'assets' / folder
            self.makeFolder(path)

            folderUrl = '/'.join([baseUrl, folder])
            datas = self.fetchFileData(folderUrl)

            if datas and len(list(path.glob('*.*'))) != len(datas):
                for data in datas:
                    filePath: Path = path / data['name']
                    if not filePath.exists():
                        try:
                            urllib.request.urlretrieve(
                                data['download_url'],
                                filePath,
                                reporthook=lambda bn, bs, ts, _fn=f'{folder}/{data["name"]}': self._reportProgress(_fn, bn, bs, ts),
                            )
                        except Exception as e:
                            logger.error('Failed while downloading %s/%s. Error: %s', folder, data['name'], e)

        self._onFinished()

    def _reportProgress(self, file_name: str, block_num: int, block_size: int, total_size: int) -> None:
        if total_size > 0:
            percent = (block_num * block_size / total_size) * 100
        else:
            percent = 0.0
        self._onProgress(file_name, percent)

    def _onProgress(self, file_name: str, percent: float) -> None:
        """Override to receive download progress."""

    def _onFinished(self) -> None:
        """Override or connect to be notified on completion."""
