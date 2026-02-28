<div align="center">

<img src="icons/altbooster.png" width="120" alt="ALT Booster Logo"/>

# ALT Booster

**Утилита обслуживания системы ALT Linux с графическим интерфейсом GTK4/Adwaita**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-ALT%20Linux-informational)](https://altlinux.org)
[![GTK](https://img.shields.io/badge/GTK-4.0-green)](https://gtk.org)
[![Python](https://img.shields.io/badge/Python-3.10+-yellow)](https://python.org)

</div>

<div align="center">
  <img src="previewb.png" alt="Интерфейс ALT Booster v 4.0" width="800">
</div>

---

## О проекте

ALT Booster — нативное GTK4-приложение для рутинного обслуживания системы ALT Linux.  
Запускается от обычного пользователя, привилегированные команды выполняются через `sudo`.

## Возможности

Утилита разделена на тематические вкладки, покрывающие рутинные задачи по настройке системы:

* **Система и интерфейс:** Активация `sudo`, настройка раскладки клавиатуры, подключение репозитория Flathub, включение дробного масштабирования GNOME и оптимизация файлового менеджера Nautilus.
* **Оптимизация и обслуживание:** Очистка пакетного кэша `apt`, удаление неиспользуемых библиотек `flatpak`, сжатие логов `journald`, SSD TRIM, а также балансировка, дефрагментация и проверка (scrub) разделов Btrfs.
* **Управление приложениями:** Встроенный каталог популярных программ (браузеры, мессенджеры, редакторы) из Flathub и EPM с возможностью быстрой установки и удаления.
* **Внешний вид и Терминал:** Установка тем иконок Papirus и цветных папок, автоматическая настройка современного терминала Ptyxis, оболочки ZSH (zplug, алиасы) и утилиты Fastfetch.
* **Профильные настройки:** Специализированные модули для видеокарт AMD (активация разгона в GRUB, установка LACT и профилей) и видеоредактора DaVinci Resolve (установка AAC-кодека, звука Fairlight и управление путями к кэшу).

## Требования

- ALT Linux (Sisyphus / p10 / p11)
- Python 3.10+
- GTK 4.0 + libadwaita
- GNOME или совместимый Wayland DE

## Установка

### 1. Клонировать и установить

```bash
git clone https://github.com/plafonlinux/altbooster.git
cd altbooster
./install.sh
```

### 2. Запустить

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
├── icons/                      # Графические ресурсы (иконки .svg, .png)
├── src/                        # Исходный код приложения
│   ├── builtin_actions/        # Обработчики для динамических вкладок (amd, appearance, terminal)
│   ├── modules/                # JSON-конфигурации для генерации Data-Driven UI
│   ├── ui/                     # Статические страницы (Начало, DaVinci) и классы компонентов
│   ├── backend.py              # Исполнение системных вызовов (sudo/epm) и проверки статусов
│   ├── config.py               # Управление конфигурацией и сохранённым состоянием
│   ├── dynamic_page.py         # Движок отрисовки интерфейса на основе JSON
│   ├── main.py                 # Точка входа в приложение
│   └── widgets.py              # Фабрики компонентов Adwaita/GTK (кнопки, статусы)
├── install.sh                  # Скрипт автоматической установки в систему
├── uninstall.sh                # Скрипт для удаления приложения
├── pyproject.toml              # Метаданные проекта и зависимости (PEP 621)
├── LICENSE                     # Юридическая информация (MIT)
├── CHANGELOG.md                # История версий
└── README.md                   # Главная страница репозитория
```

## Лицензия

[MIT](LICENSE) © 2026 PLAFON
