# Changelog

Все значимые изменения в этом проекте документируются здесь.
Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/).

## [2.0.0] — 2026-02-23

### Добавлено
- Меню «⋮» с пунктами «О приложении», «Очистить лог», «Сбросить настройки»
- Диалог «О приложении» (Adw.AboutDialog) с лицензией MIT и ссылками
- Вкладка DaVinci Resolve: спойлер «Первичная настройка» (PostInstall, AMD ROCm, AAC, Fairlight)
- Поддержка AMD Radeon ROCm для DaVinci Resolve
- Задачи обслуживания: Scrub Btrfs (проверка и исправление ошибок данных)
- Автоотключение Btrfs-задач на системах с EXT4
- Новая иконка приложения (жёлтый фон, символ Λ)

### Изменено
- Нативные иконки Adwaita вместо emoji-галочек ✅/❌
- EPM-команды переведены на `sudo -A` + SUDO_ASKPASS (устранены зависания)
- Дефрагментация теперь работает на всех Btrfs-разделах через `findmnt`
- Полный рефакторинг: нет однострочников через `;`, type hints, docstrings

### Исправлено
- Зависание при `epm update` и `epm full-upgrade`
- Многократный вызов `_epm_fin` при обновлении системы
- `UnboundLocalError` в `_build_fairlight_section`
- `AttributeError: Image has no attribute set_label` в `_post_done`
- Дефрагментация падала с `Text file busy` на системных файлах

## [1.0.0] — 2025-12-01

### Добавлено
- Первый публичный релиз
- Четыре вкладки: Настройки, Приложения, DaVinci Resolve, Обслуживание
- 23 приложения из Flathub и EPM
- Кэширование состояния в `~/.config/altbooster/state.json`
