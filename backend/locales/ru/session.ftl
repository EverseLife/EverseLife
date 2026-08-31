# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Что говорит сама сессия: разбор команды, а не правила мира.
# Формат — Fluent (D-251). Ключи ASCII, тексты — язык игры.

session-command-unnamed = команда не названа
session-command-unknown = нет такой команды: { $cmd }
session-need-hello = сначала hello
session-field-missing = команде не хватает поля «{ $field }»
session-not-understood = команда не понята: { $why }
session-server-failed = сервер не справился с командой; это записано
session-locale-unknown = такого языка нет: { $locale }
