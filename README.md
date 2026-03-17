<div align="center">

<img src="icons/altbooster.png" width="120" alt="ALT Booster Logo"/>

# ALT Booster

Умная утилита для тонкой настройки и обслуживания ALT Linux. Создана с фокусом на безопасность, надёжность и современный интерфейс на GTK4/Adwaita.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-ALT%20Linux-informational)](https://altlinux.org)
[![ALT Linux Sisyphus](https://img.shields.io/badge/ALT_Linux-Sisyphus-yellow)](https://packages.altlinux.org/ru/sisyphus/srpms/plafon-altbooster/)
[![GTK](https://img.shields.io/badge/GTK-4.0-green)](https://gtk.org)
[![Python](https://img.shields.io/badge/Python-3.10+-yellow)](https://python.org)

</div>

<div align="center">
  <img src="previewb.png" alt="Интерфейс ALT Booster" width="800">
</div>

---

## О проекте

**ALT Booster** — нативное GTK4-приложение для настройки и обслуживания ALT Linux Workstation (GNOME).

Большинство утилит системного администрирования — это либо скрипты без интерфейса, либо перегруженные GUI-оболочки над ними. ALT Booster занимает другую нишу: полноценное приложение, где безопасность и UX продуманы с самого начала, а не прикручены сверху.

В отличие от простых скриптов, ALT Booster предлагает:
- **Безопасное выполнение команд:** Изолированное и безопасное повышение привилегий через `sudo` с опциональной интеграцией со связкой ключей GNOME.
- **Отзывчивый интерфейс:** Все длительные операции выполняются в фоновом потоке, не замораживая приложение.
- **Умное решение проблем:** Утилита автоматически решает распространённые проблемы, такие как установка недостающих зависимостей или обновление кэша пакетов при ошибках.
- **Надёжность:** Продуманные проверки (например, поиск зависимостей перед удалением пакета) защищают систему от случайных поломок.

## Ключевые возможности

- Менеджер приложений — EPM и Flatpak/Flathub в одном интерфейсе
- Менеджер расширений GNOME — поиск, установка, управление
- Обслуживание системы — задачи описаны в JSON, легко расширяемо
- Резервное копирование — TymeSync с расписанием через systemd-таймеры
- Настройки AMD/Intel — планировщики, производительность, видеодрайвера
- Самообновление — через git или архив с GitHub

## Требования

- ALT Linux (Sisyphus / p10 / p11)
- Python 3.10+
- GTK 4.0 + libadwaita
- GNOME или совместимый Wayland DE
- git

## Установка

### 1. Установите git

```bash
su - -c 'apt-get install git'
```

### 2. Клонировать и установить

```bash
git clone https://github.com/plafonlinux/altbooster.git
cd altbooster
./install.sh
```

### 3. Запустить

```bash
altbooster
# или через меню приложений GNOME
```

### Удаление

```bash
./uninstall.sh
```

## Структура проекта

```
altbooster/
├── icons/                     # Графические ресурсы (иконки .svg, .png)
│   ├── altbooster.svg/.png    # Иконка приложения
│   └── hicolor/               # Иконки для системных тем GTK
├── src/                       # Исходный код приложения
│   ├── altbooster.py          # Точка входа в приложение
│   ├── core/                  # Бэкенд-модули для взаимодействия с системой
│   │   ├── backend.py         # Фасад, агрегирующий API всего бэкенда
│   │   ├── borg.py            # Операции BorgBackup (низкоуровневые)
│   │   ├── btrfs.py           # Снэпшоты и операции Btrfs
│   │   ├── checks.py          # Функции проверки состояния системы
│   │   ├── config.py          # Пути, версия, state.json, константы
│   │   ├── gsettings.py       # Обёртки для gsettings/dconf
│   │   ├── packages.py        # Логика epm и flatpak
│   │   ├── privileges.py      # Безопасное выполнение команд через sudo/pkexec
│   │   └── tweaks.py          # Реализация конкретных системных твиков
│   ├── modules/               # JSON-файлы, описывающие UI для Data-Driven страниц
│   ├── tabs/                  # Модули вкладок приложения
│   │   ├── amd.py             # Вкладка «AMD»
│   │   ├── amd_actions.py     # Действия для AMD-страницы
│   │   ├── apps.py            # Вкладка «Приложения»
│   │   ├── davinci.py         # Вкладка «DaVinci Resolve»
│   │   ├── extensions.py      # Вкладка «Расширения GNOME»
│   │   ├── flatpak.py         # Вкладка «Flatpak»
│   │   ├── intel.py           # Вкладка «Intel» (scx_meteor)
│   │   ├── maintenance.py     # Вкладка «Обслуживание»
│   │   ├── setup.py           # Вкладка «Начало»
│   │   ├── terminal.py        # Вкладка «Терминал»
│   │   ├── terminal_actions.py# Действия для терминальной страницы
│   │   └── timesync/          # Вкладка «Резервная копия» (Time Machine / BorgBackup)
│   │       ├── page.py        # Главная страница вкладки
│   │       ├── borg.py        # UI-логика резервного копирования
│   │       ├── schedule.py    # Настройка расписания (systemd-таймеры)
│   │       ├── snapshots.py   # Просмотр и управление снэпшотами
│   │       ├── restore.py     # Восстановление из резервной копии
│   │       ├── pickers.py     # Диалоги выбора файлов/директорий
│   │       ├── metadata.py    # Метаданные архивов
│   │       └── summary.py     # Сводный диалог после операций
│   └── ui/                    # Переиспользуемые UI-компоненты
│       ├── common.py          # Общие вспомогательные функции UI
│       ├── dialogs.py         # Кастомные диалоговые окна (пароль, редактор)
│       ├── dynamic_page.py    # Движок, генерирующий UI на основе JSON
│       ├── install_preview_dialog.py # Диалог предпросмотра установки
│       ├── rows.py            # Переиспользуемые Adw.ActionRow / Adw.ExpanderRow
│       ├── widgets.py         # Фабрики стандартных виджетов Adwaita/GTK
│       └── window.py          # AltBoosterWindow: layout, аутентификация, логи
├── install.sh                 # Скрипт установки
├── uninstall.sh               # Скрипт удаления
├── Makefile                   # Сборка и установка через make
├── pyproject.toml             # Метаданные проекта и зависимости (PEP 621)
├── CHANGELOG.md               # История изменений
├── CONTRIBUTING.md            # Руководство для участников
└── README.md                  # Этот файл
```

## Лицензия

[MIT](LICENSE) © 2026 PLAFON
