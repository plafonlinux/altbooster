<div align="center">

<img src="altbooster.svg" width="120" alt="ALT Booster Logo"/>

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

| Задача | Команда |
|--------|---------|
| 🗑️ Очистка кэша APT | `apt-get clean` |
| 🧩 Уборка Flatpak | `flatpak uninstall --unused` |
| 📋 Сжатие журналов | `journalctl --vacuum-time=14d` |
| 💾 Кэш DaVinci Resolve | `find ... -delete` |
| 🖥️ Балансировка Btrfs | `btrfs balance start` |
| 🔄 Дефрагментация Btrfs | `btrfs filesystem defragment` |
| ⚡ SSD TRIM | `fstrim -av` |

## Требования

- ALT Linux (Sisyphus / p10 / p11)
- Python 3.10+
- GTK 4.0 + libadwaita
- GNOME или совместимый Wayland DE

## Установка

### 1. Зависимости

```bash
sudo apt-get install python3-module-pygobject3 libgtk4-gir libadwaita-gir
