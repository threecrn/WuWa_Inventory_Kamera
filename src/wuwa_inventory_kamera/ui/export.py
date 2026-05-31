"""
wuwa_inventory_kamera.ui.export
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Simple in-app export interface for converting character + echo JSON files
into WutheringTools format.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
)

from ..exporter.wutheringtools import write_wutheringtools_export
from .config import cfg


class ExportInterface(QWidget):
    """UI for selecting input files and writing a WutheringTools export."""

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName('exportUI')
        self.__init_ui()

    def __init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        title = BodyLabel('WutheringTools Export', self)
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        root.addWidget(title)

        self.charactersPath = LineEdit(self)
        self.charactersPath.setPlaceholderText('Path to characters_wuwainventorykamera.json or scan_result.json')

        self.echoesPath = LineEdit(self)
        self.echoesPath.setPlaceholderText('Path to echoes_wuwainventorykamera.json or scan_result.json')

        self.outputPath = LineEdit(self)
        self.outputPath.setPlaceholderText('Output JSON path (optional)')

        self.languageCode = LineEdit(self)
        self.languageCode.setPlaceholderText('Language code (default: en)')
        self.languageCode.setText('en')

        root.addLayout(self.__build_file_row('Characters JSON', self.charactersPath, self._pick_characters))
        root.addLayout(self.__build_file_row('Echoes JSON', self.echoesPath, self._pick_echoes))
        root.addLayout(self.__build_file_row('Output JSON', self.outputPath, self._pick_output))
        root.addLayout(self.__build_file_row('Language', self.languageCode, None))

        self.runExport = PrimaryPushButton(FIF.SEND, 'Export to WutheringTools', self)
        self.runExport.clicked.connect(self._run_export)
        root.addWidget(self.runExport)
        root.addStretch(1)

    def __build_file_row(self, label_text: str, line_edit: LineEdit, browse_handler) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        label = BodyLabel(label_text, self)
        label.setFixedWidth(110)
        row.addWidget(label)
        row.addWidget(line_edit, 1)

        if browse_handler is not None:
            browse_button = PushButton('Browse', icon=FIF.FOLDER, parent=self)
            browse_button.clicked.connect(browse_handler)
            row.addWidget(browse_button)

        return row

    def _default_export_dir(self) -> str:
        configured = str(cfg.get(cfg.exportFolder) or '').strip()
        if configured:
            path = Path(configured)
            if path.is_dir():
                return str(path)
        return str(Path.cwd())

    def _pick_characters(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select character JSON',
            self._default_export_dir(),
            'JSON files (*.json)',
        )
        if path:
            self.charactersPath.setText(path)

    def _pick_echoes(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select echo JSON',
            self._default_export_dir(),
            'JSON files (*.json)',
        )
        if path:
            self.echoesPath.setText(path)

    def _pick_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Save WutheringTools export',
            str(Path.cwd() / 'wutheringtools_export.json'),
            'JSON files (*.json)',
        )
        if path:
            self.outputPath.setText(path)

    def _run_export(self) -> None:
        characters_path = Path(self.charactersPath.text().strip())
        echoes_path = Path(self.echoesPath.text().strip())

        if not characters_path.is_file() or not echoes_path.is_file():
            InfoBar.warning(
                title='Missing input files',
                content='Please select valid characters and echoes JSON files.',
                orient=Qt.Orientation.Vertical,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return

        output_text = self.outputPath.text().strip()
        output_path = Path(output_text) if output_text else characters_path.parent / 'wutheringtools_export.json'
        language = self.languageCode.text().strip() or 'en'

        try:
            written = write_wutheringtools_export(
                characters_path=characters_path,
                echoes_path=echoes_path,
                output_path=output_path,
                language=language,
            )
        except Exception as exc:
            InfoBar.error(
                title='Export failed',
                content=str(exc),
                orient=Qt.Orientation.Vertical,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=8000,
                parent=self,
            )
            return

        self.outputPath.setText(str(written))
        InfoBar.success(
            title='Export complete',
            content=f'Wrote {written}',
            orient=Qt.Orientation.Vertical,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )
