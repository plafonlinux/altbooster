"""Проверка поддержки sched_ext в текущем (загруженном) ядре."""

from __future__ import annotations

import os

SYSFS_SCHED_EXT = "/sys/kernel/sched_ext"

# Виртуальный пакет образа ядра для apt-get install: подтягивает актуальную сборку выбранной
# линии в репозитории ALT. Имя при необходимости обновляют майнтейнеры / релизы Booster.
KERNEL_IMAGE_SCHED_EXT = "kernel-image-6.18"


def has_sched_ext() -> bool:
    return os.path.exists(SYSFS_SCHED_EXT)
