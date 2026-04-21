from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from qfluentwidgets import ComboBox, LineEdit, PrimaryPushButton, SettingCard, SwitchButton


class TextCard(SettingCard):
    def __init__(self, icon, title: str, content: str, placeholder: str = "", parent=None):
        super().__init__(icon, title, content, parent)
        self.line_edit = LineEdit()
        if placeholder:
            self.line_edit.setPlaceholderText(placeholder)
        self.line_edit.setFixedWidth(220)
        self.hBoxLayout.addWidget(self.line_edit)
        self.hBoxLayout.addSpacing(16)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def set_text(self, value: str) -> None:
        self.line_edit.setText(value)


class SwitchCard(SettingCard):
    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.switch = SwitchButton()
        self.hBoxLayout.addWidget(self.switch)
        self.hBoxLayout.addSpacing(16)

    def is_checked(self) -> bool:
        return self.switch.isChecked()

    def set_checked(self, value: bool) -> None:
        self.switch.setChecked(value)


class ComboCard(SettingCard):
    def __init__(self, icon, title: str, content: str, items: list, parent=None):
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox()
        self.combo.addItems(items)
        self.combo.setFixedWidth(160)
        self.hBoxLayout.addWidget(self.combo)
        self.hBoxLayout.addSpacing(16)

    def current_index(self) -> int:
        return self.combo.currentIndex()

    def set_index(self, idx: int) -> None:
        self.combo.setCurrentIndex(idx)


class ButtonCard(SettingCard):
    def __init__(self, icon, title: str, content: str, button_text: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.button = PrimaryPushButton(button_text)
        self.button.setMinimumWidth(180)
        self.hBoxLayout.addWidget(self.button)
        self.hBoxLayout.addSpacing(16)


class StatusCard(SettingCard):
    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(220)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.hBoxLayout.addWidget(self.value_label)
        self.hBoxLayout.addSpacing(16)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)
