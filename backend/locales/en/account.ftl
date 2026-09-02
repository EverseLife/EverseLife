# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The account, signing in, self-description (D-187), the ledger (D-190) and the
# alpha debug widget (D-229).
#
# A message value is one line: a line break in the Fluent source lands in the
# text of the refusal. The variants of a select { $x -> ... } go one per line,
# and those breaks do not land in the text.

## Account and signing in

account-bad-email = that email does not look right
account-short-password = the password is shorter than { $limit } characters
account-email-taken = this email is already taken
account-bad-credentials = the email or the password is wrong
account-empty-token = the token is empty
account-session-expired = the session has expired: sign in again

## Self-description

account-no-name = no name given
account-long-name = the name is longer than { $limit } characters
account-long-surname = the surname is longer than { $limit } characters
account-long-about = the description is longer than { $limit } characters
account-age-not-a-number = age is a number
account-age-out-of-range = age runs from { $min } to { $max }
account-no-such-line = there is no such line
account-line-not-ready = this line is still in development

## The ledger (D-190). Not the player alone reads these: an operation that does
## not balance is a refusal handed to the engine, and it owes it the sum and the
## reason.

ledger-no-postings = an operation with no postings
ledger-unbalanced = the postings do not balance: sum { $total }, reason { $reason }. Money moves, it does not appear (И2)
ledger-not-positive = a transfer must be positive, got { $amount }
ledger-insufficient = the account does not hold that much: has { $have }, needs { $need }

## The alpha debug widget (D-229)

alpha-no-such-thing = there is no such thing in this world: { $goods }
alpha-amount-not-positive = the amount must be greater than zero
alpha-amount-too-big = there is no such amount: no more than { $limit }
alpha-quality-out-of-range = quality runs from { $min } to { $max }
alpha-liquid-nowhere = “{ NAME($goods) }” is a liquid, and there is nowhere to pour it: take a canister in hand or stand where a tank stands. A liquid does not live in bare palms
alpha-not-your-body = this body is not this identity's
alpha-nowhere = a print goes into the hands or onto the floor, not “{ $where }”
alpha-no-grid = there is no city grid here: energy is printed into a pool from inside a city's built-up area
