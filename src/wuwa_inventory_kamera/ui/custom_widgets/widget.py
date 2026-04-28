from typing import Union, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QValidator
from PySide6.QtWidgets import (
    QHBoxLayout, QVBoxLayout, QWidget,
    QSizePolicy,
)

from qfluentwidgets import (
    LineEdit, FluentIconBase, SettingCard,
    SpinBox, Signal, BodyLabel,
    PushButton, qconfig,
)


class FieldSettingCard(SettingCard):
    """Setting card with a text field."""

    def __init__(self, configItem, icon: Union[str, QIcon, FluentIconBase], title, content=None, max_length=None, parent=None):
        super().__init__(icon, title, content, parent)
        self.configItem = configItem
        self.max_length = max_length

        self.lineEdit = LineEdit(self)
        self.hBoxLayout.addWidget(self.lineEdit, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

        self.lineEdit.setText(str(qconfig.get(configItem)))
        self.lineEdit.textChanged.connect(self._onTextChanged)
        configItem.valueChanged.connect(self.setValue)

    def _onTextChanged(self, text: str):
        if self.max_length is not None and len(text) > self.max_length:
            text = text[:self.max_length]
            self.lineEdit.setText(text)
        qconfig.set(self.configItem, text)

    def setValue(self, value):
        if self.max_length is not None and len(value) > self.max_length:
            value = value[:self.max_length]
        if self.lineEdit.text() == value:
            return
        self.lineEdit.setText(value)
        qconfig.set(self.configItem, value)


class CustomSpinBox(SpinBox):
    def __init__(self, placeholder='Value', minRange=0, maxRange=100, parent=None):
        super().__init__(parent)
        self.placeholder = placeholder
        self.setRange(minRange, maxRange)

    def textFromValue(self, value):
        return f"{self.placeholder}: {value}"

    def valueFromText(self, text):
        try:
            return int(text.split(": ")[1])
        except (ValueError, IndexError):
            return 0

    def validate(self, text, pos):
        if text.startswith(f"{self.placeholder}: "):
            number_part = text[len(f"{self.placeholder}: "):]
            if number_part.isdigit():
                value = int(number_part)
                if 0 <= value <= 100:
                    return QValidator.Acceptable, text, pos
        return QValidator.Invalid, text, pos

    def fixup(self, text):
        try:
            value = int(text.split(": ")[1])
        except (ValueError, IndexError):
            value = self.value()
        return self.textFromValue(value)


class MultiplePushSettingCard(SettingCard):
    """Setting card with multiple push buttons aligned to the right."""

    buttonClicked = Signal(int)

    def __init__(self, texts: List[str], icon: Union[str, QIcon, FluentIconBase], title: str, content: str = None, parent=None):
        super().__init__(icon, title, content, parent)

        self.buttons = []
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setSpacing(8)
        self.buttonLayout.setContentsMargins(0, 0, 0, 0)

        self.buttonContainer = QWidget()
        self.buttonContainer.setLayout(self.buttonLayout)

        for i, text in enumerate(texts):
            button = PushButton(text)
            button.clicked.connect(lambda checked, index=i: self.buttonClicked.emit(index))
            self.buttons.append(button)
            self.buttonLayout.addWidget(button)

        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.buttonContainer, 0, Qt.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def addButton(self, text: str):
        button = PushButton(text)
        button.clicked.connect(lambda checked, index=len(self.buttons): self.buttonClicked.emit(index))
        self.buttons.append(button)
        self.buttonLayout.addWidget(button)

    def getContent(self):
        return self.contentLabel.text()


class FileItem(QWidget):
    """File item."""

    removed = Signal(QWidget)

    def __init__(self, file: str, parent=None):
        super().__init__(parent=parent)
        self.file = file
        self.hBoxLayout = QHBoxLayout(self)
        self.fileLabel = BodyLabel(file, self)
        self.removeButton = PushButton("X", self)

        self.removeButton.setFixedSize(39, 29)

        self.setFixedHeight(53)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.hBoxLayout.setContentsMargins(48, 0, 60, 0)
        self.hBoxLayout.addWidget(self.fileLabel, 0, Qt.AlignLeft)
        self.hBoxLayout.addSpacing(16)
        self.hBoxLayout.addStretch(1)
        self.hBoxLayout.addWidget(self.removeButton, 0, Qt.AlignRight)
        self.hBoxLayout.setAlignment(Qt.AlignVCenter)

        self.removeButton.clicked.connect(lambda: self.removed.emit(self))


class FileSettingCard(QWidget):
    """File setting card."""

    fileChanged = Signal(list)

    def __init__(self, config_item, title: str, content: str = None, directory="./", parent=None):
        super().__init__(parent)
        self.config_item = config_item
        self._dialogDirectory = directory
        self.title = title
        self.content = content
        self.addFileButton = PushButton(self.tr('Add file'), self)

        self.files = config_item.copy()
        self.__initWidget()

    def __initWidget(self):
        self.mainLayout = QVBoxLayout(self)
        self.titleLabel = BodyLabel(self.title, self)
        self.contentLabel = BodyLabel(self.content, self)
        self.viewLayout = QVBoxLayout()

        self.mainLayout.addWidget(self.titleLabel)
        self.mainLayout.addWidget(self.contentLabel)
        self.mainLayout.addLayout(self.viewLayout)
        self.mainLayout.addWidget(self.addFileButton)

        self.viewLayout.setSpacing(0)
        self.viewLayout.setAlignment(Qt.AlignTop)
        self.viewLayout.setContentsMargins(0, 0, 0, 0)
        for file in self.files:
            self.__addFileItem(file)

        self.addFileButton.clicked.connect(self.__showFileDialog)

    def __addFileItem(self, file: str):
        item = FileItem(file, self)
        item.removed.connect(self.__showFileDialog)
        self.viewLayout.addWidget(item)

    def __showFileDialog(self):
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.tr('Choose file'), self._dialogDirectory)
        if file_path:
            self.files.append(file_path)
            self.__addFileItem(file_path)
            self.fileChanged.emit(self.files)
