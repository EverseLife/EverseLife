# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Agentic player system: AI citizens of Everse.Life (D-224).

An agent is a player. It owns an ordinary account, pays the device fee and acts
only through the same WebSocket the browser client uses. This package never
imports the game engine: what the engine refuses to a player it refuses to the
agent, and every refusal is a finding.
"""
