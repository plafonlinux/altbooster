# AGENT.md — Контекст проекта для AI-агентов

> **Правило:** Если ты вносишь изменения в архитектуру, добавляешь/переименовываешь файлы,
> новые вкладки, паттерны, зависимости или меняешь конвенции — обнови этот файл.
> Держи его актуальным: это основной источник контекста для следующего агента.

---

## Что такое ALT Booster

Утилита настройки и оптимизации ALT Linux с GUI на GTK4 / libadwaita.
Целевая ОС: ALT Linux Sisyphus / p10 / p11, рабочий стол GNOME.
Запускается от обычного пользователя, sudo-пароль вводится один раз при старте.

- **Application ID**: `ru.altbooster.app`
- **Entrypoint**: `src/main.py` → `AltBoosterApp(Adw.Application)`
- **Запуск**: `python3 src/main.py` (из корня) или `altbooster` (после install.sh)
- **Python**: 3.11+, зависимости только системные (GTK4 / libadwaita), pip не нужен
- **Лицензия**: MIT

---

## Структура файлов

```
altbooster/
├── src/
│   ├── main.py                  # Entrypoint, AltBoosterApp
│   ├── config.py                # Состояние (~/.config/altbooster/state.json)
│   ├── backend.py               # Фасад: re-export из system/* + builtin_actions/*
│   ├── widgets.py               # GTK4-фабрики (make_button, make_icon, ...)
│   ├── dynamic_page.py          # Data-Driven движок: JSON → UI
│   ├── ui/
│   │   ├── __init__.py          # export PlafonWindow = AltBoosterWindow
│   │   ├── window.py            # Главное окно, 8 вкладок, лог-панель
│   │   ├── rows.py              # SettingRow, AppRow, TaskRow
│   │   ├── dialogs.py           # PasswordDialog, AppEditDialog
│   │   ├── common.py            # load_module(name) → dict из modules/*.json
│   │   ├── setup_page.py        # Вкладка «Начало» (система, EPM, клавиатура)
│   │   ├── apps_page.py         # Вкладка «Приложения» (CRUD + mass install)
│   │   ├── extensions_page.py   # Вкладка «Расширения» (GNOME Extensions)
│   │   ├── davinci_page.py      # Вкладка «DaVinci Resolve»
│   │   └── maintenance_page.py  # Вкладка «Обслуживание»
│   ├── system/
│   │   ├── privileges.py        # run_privileged, run_epm, sudo-пароль
│   │   ├── checks.py            # is_sudo_enabled, is_flathub_enabled, ...
│   │   ├── gsettings.py         # run_gsettings, gsettings_get
│   │   └── tweaks.py            # apply_vm_dirty, patch_drive_menu, install_aac_codec
│   ├── builtin_actions/
│   │   ├── __init__.py          # BUILTIN_REGISTRY: {fn_name: callable}
│   │   ├── appearance.py        # Papirus, Adwaita, папки Papirus
│   │   ├── terminal.py          # Ptyxis, ZSH, fastfetch, шрифты, алиасы
│   │   └── amd.py               # Разгон, LACT, GRUB, reboot
│   └── modules/
│       ├── appearance.json      # Темы иконок, цвета папок, обои
│       ├── terminal.json        # Терминал, ZSH, fastfetch
│       ├── amd.json             # AMD Radeon конфигурация
│       ├── apps.json            # 20+ приложений (Flatpak + RPM)
│       └── maintenance.json     # Задачи очистки
├── icons/                       # SVG/PNG иконки приложения
├── install.sh / uninstall.sh    # Установка в /opt/altbooster
├── pyproject.toml               # PEP 621, ruff (line-length=100)
├── CHANGELOG.md
└── CONTRIBUTING.md
```

---

## Вкладки (порядок в ViewStack)

| # | Название | Icon | Класс | Источник |
|---|----------|------|-------|----------|
| 1 | Начало | go-home-symbolic | `SetupPage` | Python |
| 2 | Приложения | flathub-symbolic | `AppsPage` | Python + apps.json |
| 3 | Расширения | application-x-addon-symbolic | `ExtensionsPage` | Python |
| 4 | Внешний вид | preferences-desktop-wallpaper-symbolic | `DynamicPage` | appearance.json |
| 5 | Терминал | utilities-terminal-symbolic | `DynamicPage` | terminal.json |
| 6 | AMD Radeon | video-display-symbolic | `DynamicPage` | amd.json |
| 7 | DaVinci Resolve | davinci-symbolic | `DaVinciPage` | Python |
| 8 | Обслуживание | emblem-system-symbolic | `MaintenancePage` | Python |

Добавить новую вкладку: создать класс/файл → импортировать в `window.py` → добавить в список `for widget, name, title, icon in [...]`.

---

## Ключевые паттерны

### SettingRow (rows.py)
Строка настройки с check-функцией. На первом запуске (нет state.json) всегда опрашивает систему.

```python
SettingRow(icon, title, subtitle, btn_label, on_activate, check_fn, state_key, done_label)
```

- Если `config.state_get(state_key) is True` → сразу показывает ✓ (кэш)
- Если `"kbd" not in state_key and check_fn is not None` → запускает `_refresh()` в потоке
- `set_done(ok)` → сохраняет состояние + обновляет UI

### AppRow (rows.py)
Установка/удаление приложения. Всегда вызывает `check_app_installed()` при инициализации (кэш не используется).

### SettingRow с клавиатурой
Три строки keyboard не имеют `check_fn` (= None) и определяются через `_detect_kbd_mode()` в отдельном потоке в SetupPage.

### DynamicPage (dynamic_page.py)
JSON-движок для вкладок Внешний вид / Терминал / AMD.
- Строит UI из `groups[].rows[]`
- Запускает `_poll_checks()` при старте (игнорирует state-кэш)
- После успешного action → `page.refresh()` перепроверяет все строки

### Фоновые потоки + GTK
**Правило**: все `subprocess.run` и IO — в `threading.Thread(daemon=True)`.
Обновление виджетов только через `GLib.idle_add(fn, args)`.

### run_privileged / run_epm (privileges.py)
- `run_privileged(cmd, on_line, on_done)` — async, sudo -S
- `run_privileged_sync(cmd, on_line)` — blocking (для вызова из потока)
- `run_epm(cmd, on_line, on_done)` — async, sudo -A + SUDO_ASKPASS
- epm/epmi автоматически устанавливают eepm если не найден

### Состояние (config.py)
```python
config.state_get(key)          # читать
config.state_set(key, value)   # писать + сохранить на диск
config.reset_state()           # очистить всё
```
Файл: `~/.config/altbooster/state.json`

### Виджеты (widgets.py)
```python
make_button(label, width=130, style="suggested-action") → Gtk.Button
make_icon(name, size=22)                                → Gtk.Image
make_status_icon()                                      → Gtk.Image
set_status_ok(icon) / set_status_error(icon) / clear_status(icon)
make_suffix_box(*widgets)                               → Gtk.Box
make_scrolled_page()                                    → (ScrolledWindow, Body Gtk.Box)
```

---

## Data-Driven UI: типы check и action (dynamic_page.py)

### Check types
| type | Что проверяет |
|------|--------------|
| `rpm` | `rpm -q <value>` |
| `flatpak` | `flatpak list \| grep <value>` |
| `which` | `which <value>` |
| `path` | `os.path.exists(~/<value>)` |
| `systemd` | `systemctl is-enabled <value>` |
| `gsettings` | schema.key == expected |
| `gsettings_contains` | schema.key contains value |
| `builtin` | `BUILTIN_REGISTRY["fn"](page, arg)` |

### Action types
| type | Что делает |
|------|-----------|
| `privileged` | `backend.run_privileged_sync(cmd)` |
| `epm` | `backend.run_epm_sync(cmd)` |
| `shell` | `subprocess.run(cmd)` без sudo |
| `gsettings` | `backend.run_gsettings(args)` |
| `open_url` | `Gio.AppInfo.launch_default_for_uri(url)` |
| `builtin` | `BUILTIN_REGISTRY["fn"](page, arg)` |

### Row types
| type | Виджет |
|------|--------|
| `command_row` | Кнопка + статус |
| `dropdown_row` | Gtk.DropDown + кнопка |
| `file_row` | File picker + кнопка |

---

## Вкладка «Расширения» (extensions_page.py)

### Секции
1. **Менеджер расширений** — установка `com.mattjakeman.ExtensionManager` через flatpak
2. **Рекомендуемые** — 5 расширений (AppIndicator, Vitals, Just Perfection, Dash to Dock, Dash to Panel)
3. **Установить по ID** — поле ввода числового ID с extensions.gnome.org
4. **Установленные** — Adw.ExpanderRow × 2 (Пользовательские / Системные)

### Установка через gext
- `_gext_path()` ищет бинарник в PATH и `~/.local/bin/gext`
- Если не найден: автоматически запускает `pip3 install gnome-extensions-cli --user` в фоне
- Команда установки: `[gext_path, "install", uuid_or_id]`

### Список установленных
- Читает `metadata.json` напрямую из `~/.local/share/gnome-shell/extensions/` и `/usr/share/gnome-shell/extensions/`
- Статус включено/выключено: `gnome-extensions list --enabled`
- Каждая строка: `Gtk.Switch` (enable/disable) + кнопка удаления
- Пересоздание группы при обновлении (`Adw.PreferencesGroup` не поддерживает remove строк)

### Удаление системных расширений
Цепочка проверок в `_do_delete_system_ext()`:
1. `rpm -qf <ext_path>` → найден пакет?
2. Если да: `rpm -q --whatrequires <pkg>` → есть зависимости?
   - Есть: лог с причиной, стоп
   - Нет: `sudo rpm -e <pkg>`
3. Если нет (не RPM): `sudo rm -rf <ext_path>`

---

## Конфигурация

| Файл | Назначение |
|------|-----------|
| `~/.config/altbooster/state.json` | Кэш статусов проверок, пути DaVinci |
| `~/.config/altbooster/window.json` | Размер окна, позиция разделителя |

---

## Как не сломать существующее

- **SettingRow с `check_fn`**: на первом запуске (нет state.json) всегда опрашивает систему. Не убирай `_refresh()` и не заменяй check_fn на None без причины.
- **Keyboard rows**: `check_fn=None`, обновляются через `_detect_kbd_mode()` в отдельном потоке.
- **AppRow**: всегда вызывает `check_app_installed()`, кэш не задействован.
- **DynamicPage**: `_poll_checks()` запускается при каждом `__init__` и после успешного action.
- **GTK thread**: любое изменение виджета только из main thread — используй `GLib.idle_add`.
- **sudo пароль**: хранится в `_sudo_password` с `_sudo_lock`. Не трогай без необходимости.
- **`state_key=""`** у строки «Обновить систему»: намеренно, `set_done(False)` всегда сбрасывает кнопку в активное состояние.
