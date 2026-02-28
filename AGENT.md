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
- **Python**: 3.11+, зависимости только системные (GTK4 / libadwaita), pip не нужен (утилита сама установит `gext` при необходимости).
- **Лицензия**: MIT

---

## Структура файлов

```
altbooster/
├── src/
│   ├── main.py                  # Entrypoint, AltBoosterApp
│   ├── config.py                # Управление состоянием (~/.config/altbooster/state.json)
│   ├── backend.py               # Фасад: re-export из system/* для удобного доступа из UI
│   ├── widgets.py               # GTK4-фабрики (make_button, make_icon, ...)
│   ├── dynamic_page.py          # Data-Driven движок: JSON → UI
│   ├── ui/
│   │   ├── window.py            # Главное окно, сборка вкладок, лог-панель
│   │   ├── rows.py              # Ключевые компоненты: SettingRow, AppRow, TaskRow
│   │   ├── dialogs.py           # PasswordDialog (с libsecret), AppEditDialog (с под-страницей)
│   │   └── *.py                 # Остальные статически-определенные вкладки
│   ├── system/
│   │   ├── privileges.py        # run_privileged (sudo -S), run_epm (sudo -A + SUDO_ASKPASS)
│   │   ├── checks.py            # is_sudo_enabled, is_flathub_enabled, ...
│   │   ├── gsettings.py         # run_gsettings, gsettings_get
│   │   ├── packages.py          # Зарезервировано для будущего функционала
│   │   └── tweaks.py            # Cкрипты-твики (apply_vm_dirty, etc.)
│   ├── builtin_actions/
│   │   ├── __init__.py          # Регистрация "встроенных" функций для DynamicPage
│   │   └── *.py                 # Файлы с реализацией Python-действий для DynamicPage
│   └── modules/
│       ├── *.json               # JSON-описания для Data-Driven UI (DynamicPage, AppsPage, etc.)
├── icons/                       # SVG/PNG иконки приложения
├── install.sh / uninstall.sh    # Установка в /opt/altbooster
└── pyproject.toml               # Метаданные проекта, ruff, версия для пакета
```

---

## Вкладки (порядок в Adw.ViewStack)

| # | Название | Icon | Класс/Источник | Описание |
|---|----------|------|----------------|----------|
| 1 | Начало | go-home-symbolic | `SetupPage` | Настройки системы, EPM, клавиатура |
| 2 | Приложения | flathub-symbolic | `AppsPage` | Каталог приложений из `apps.json` |
| 3 | Расширения | application-x-addon-symbolic | `ExtensionsPage` | Менеджер расширений GNOME |
| 4 | Внешний вид | preferences-desktop-wallpaper-symbolic | `AppearancePage` | Установка тем, иконок, обоев |
| 5 | Терминал | utilities-terminal-symbolic | `TerminalPage` | Настройка терминала |
| 6 | AMD Radeon | video-display-symbolic | `DynamicPage` | Управление разгоном из `amd.json` |
| 7 | DaVinci Resolve | davinci-symbolic | `DaVinciPage` | Настройки для DaVinci Resolve |
| 8 | Обслуживание | emblem-system-symbolic | `MaintenancePage` | Задачи очистки из `maintenance.json` |

---

## Ключевые паттерны

### "Самовосстановление" и отказоустойчивость
Приложение активно пытается решить проблемы до того, как они вызовут ошибку:
- **Авто-установка `gext`**: Если `gext` (утилита для установки расширений GNOME) не найдена, `extensions_page` автоматически запустит `pip3 install gnome-extensions-cli --user`.
- **Авто-установка `eepm`**: Если `eepm` (EPM) не установлен, `privileges.py` автоматически установит его при первой попытке вызова `epm`.
- **Авто-обновление кэша**: Если установка приложения из `AppRow` завершается с ошибкой 404, утилита предполагает, что кэш пакетов устарел, автоматически запускает `apt-get update` и пробует установку снова.
- **Безопасное удаление**: `extensions_page` перед удалением системного расширения проверяет RPM-зависимости, чтобы не сломать систему.

### Фоновые потоки + GTK
**Правило**: все `subprocess.run` и IO — в `threading.Thread(daemon=True)`.
Обновление виджетов только через `GLib.idle_add(fn, args)`. Функции `start_progress`, `stop_progress`, `_log` в главном окне уже потокобезопасны.

### Data-Driven UI (dynamic_page.py)
- **UI из JSON**: Строит интерфейс из `groups[].rows[]` в `modules/*.json`.
- **Проверки (`check`)**: Определяют, активна ли настройка. Типы: `rpm`, `path`, `systemd`, `gsettings`, `builtin` (вызов Python-функции).
- **Действия (`action`)**: Выполняют операцию. Типы: `privileged`, `epm`, `shell`, `gsettings`, `builtin`.
- **`builtin`**: Позволяет JSON-описанию вызывать Python-функцию из `src/builtin_actions/`. Это используется для сложной логики, которую неудобно выражать в виде простой команды.

### Состояние vs. Проверка "вживую"
- **Состояние (`config.state_json`)**: Используется для кэширования состояния простых настроек (включено/выключено), чтобы не опрашивать систему при каждом запуске. `SettingRow` использует этот кэш.
- **Проверка "вживую"**: `AppRow` (статус установки приложения) и `DynamicPage` всегда опрашивают систему при инициализации, игнорируя кэш состояния, чтобы показать максимально актуальную информацию.

---

## Вкладка «Расширения» (extensions_page.py)

- **Ключевая зависимость**: Утилита `gext` (`gnome-extensions-cli`).
- **Авто-установка**: Если `gext` не найден, запускает `pip3 install gnome-extensions-cli --user` в фоне.
- **Установка**: Выполняется через команду `gext install <id>`.
- **Удаление системных расширений**:
  1. `rpm -qf <путь>` → Определить пакет.
  2. Если пакет найден: `rpm -q --whatrequires <пакет>` → Проверить зависимости.
  3. Если зависимостей нет → `sudo rpm -e <пакет>`.
  4. Если не RPM → `sudo rm -rf <путь>`.

---

## Конфигурация

| Файл | Назначение |
|------|-----------|
| `~/.config/altbooster/state.json` | Кэш статусов проверок (`setting_*`, `app_*`), пользовательские пути. |
| `~/.config/altbooster/window.json` | Состояние окна (размер, позиция). |
| `~/.config/altbooster/apps.json` | Пользовательская копия каталога приложений. Создаётся при первом изменении. |
| `src/modules/*.json` | Системные ("заводские") конфигурации для UI. |

---

## Как не сломать существующее

- **`SettingRow`**: На первом запуске (нет state.json) всегда опрашивает систему. Не убирай `check_fn`, если хочешь, чтобы состояние было актуальным.
- **`AppRow`**: Всегда проверяет статус установки "вживую", кэш `state.json` не используется.
- **`DynamicPage`**: Также всегда выполняет проверки "вживую" при инициализации и после каждого действия.
- **GTK thread**: Любое изменение виджета только из main thread — используй `GLib.idle_add`.
- **`run_privileged` vs `run_epm`**: `run_epm` использует `sudo -A` и более надёжен для EPM, `run_privileged` (`sudo -S`) — для всего остального.
- **`state_key=""`** у строки «Обновить систему»: Намеренно, `set_done(False)` всегда сбрасывает кнопку в активное состояние, чтобы можно было обновляться снова.
