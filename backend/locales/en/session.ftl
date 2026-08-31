# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# What the session itself says: parsing a command, not the rules of the world.
# The format is Fluent (D-251). Keys are ASCII, texts are the language of the game.

session-command-unnamed = the command is not named
session-command-unknown = no such command: { $cmd }
session-need-hello = hello first
session-field-missing = the command is missing the field “{ $field }”
session-not-understood = the command is not understood: { $why }
session-server-failed = the server could not handle the command; it is written down
session-locale-unknown = there is no such language: { $locale }
