"""
ui — интерфейс GTK4 / Adwaita для ALT Booster.

Разбит на модули:
  ui.dialogs         — PasswordDialog, AppEditDialog
  ui.rows            — SettingRow, AppRow, TaskRow
  ui.setup_page      — SetupPage
  ui.apps_page       — AppsPage
  ui.davinci_page    — DaVinciPage
  ui.maintenance_page — MaintenancePage
  ui.window          — AltBoosterWindow (главное окно)
"""

from ui.window import AltBoosterWindow

# Обратная совместимость: main.py импортирует PlafonWindow
PlafonWindow = AltBoosterWindow
