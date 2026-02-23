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
  <img src="previewb.png" alt="Интерфейс ALT Booster" width="800">
</div>

---

## О проекте

ALT Booster — нативное GTK4-приложение для рутинного обслуживания системы ALT Linux.  
Запускается от обычного пользователя, привилегированные команды выполняются через `sudo`.

## Возможности

**Обслуживание системы**

| Задача | Команда |
|--------|---------|
| 🗑️ Очистка кэша APT | `apt-get clean` |
| 🧩 Уборка Flatpak | `flatpak uninstall --unused` |
| 📋 Сжатие журналов | `journalctl --vacuum-time=14d` |
| 💾 Кэш DaVinci Resolve | `find ... -delete` |
| 🖥️ Балансировка Btrfs | `btrfs balance start` |
| 🔄 Дефрагментация Btrfs | `btrfs filesystem defragment` |
| ⚡ SSD TRIM | `fstrim -av` |

**Базовые настройки**

| Настройка | Команда |
|-----------|---------|
| 🔓 Включить sudo | `control sudowheel enabled` |
| 📦 Подключить Flathub | `apt-get install flatpak-repo-flathub` |
| 🖥️ Дробное масштабирование | `gsettings set org.gnome.mutter` |
| ⌨️ Alt+Shift / CapsLock | `gsettings set ...wm.keybindings` |
| 🔄 Автоматический TRIM | `systemctl enable fstrim.timer` |
| 📋 Лимиты журналов | `journald.conf SystemMaxUse=100M` |

**Приложения**

Установка 20+ приложений из Flathub и EPM с отображением статуса и кнопкой удаления.

**DaVinci Resolve**

| Функция | Описание |
|---------|----------|
| 🎬 Установка | `epm play davinci-resolve` |
| 🔊 AAC кодек | FFmpeg AAC Encoder Plugin |
| 🎵 Fairlight Audio | `epm -i alsa-plugins-pulse` |
| 📁 Пути к кэшу | Настраиваемые через файловый диалог |

## Требования

- ALT Linux (Sisyphus / p10 / p11)
- Python 3.10+
- GTK 4.0 + libadwaita
- GNOME или совместимый Wayland DE

## Установка

### 1. Зависимости

```bash
sudo apt-get install python3-module-pygobject3 libgtk4-gir libadwaita-gir
```

### 2. Клонировать и установить

```bash
git clone https://github.com/plafonlinux/altbooster.git
cd alt-booster
bash install.sh
```

### 3. Запустить

```bash
altbooster
# или через меню приложений GNOME
```

## Удаление

```bash
bash uninstall.sh
```

## Структура проекта

```
alt-booster/
├── src/
│   ├── main.py           # Точка входа
│   ├── ui.py             # Интерфейс (GTK4/Adwaita)
│   ├── backend.py        # Команды и проверки
│   └── config.py         # Конфиг, данные приложений
├── icons/
│   ├── altbooster.svg        # Иконка приложения
│   ├── davinci-symbolic.svg  # Иконка DaVinci
│   └── flathub-symbolic.svg  # Иконка Flathub
├── install.sh            # Установщик
├── uninstall.sh          # Деинсталлятор
└── README.md
```

## Лицензия

[MIT](LICENSE) © 2026 PLAFON
