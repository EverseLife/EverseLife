# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Учётка, вход, самоописание (D-187), счета (D-190) и отладочный виджет
# альфы (D-229).
#
# Значение сообщения — одной строкой: перенос в исходнике Fluent попадает
# в текст отказа. Варианты выбора { $x -> ... } — каждый на своей строке,
# и вот эти переносы в текст не попадают.

## Учётка и вход

account-bad-email = почта выглядит неправильно
account-short-password = пароль короче { $limit } знаков
account-email-taken = эта почта уже занята
account-bad-credentials = почта или пароль не подходят
account-empty-token = жетон пуст
account-session-expired = сессия истекла: войдите заново

## Самоописание

account-no-name = имя не названо
account-long-name = имя длиннее { $limit } знаков
account-long-surname = фамилия длиннее { $limit } знаков
account-long-about = описание длиннее { $limit } знаков
account-age-not-a-number = возраст — число
account-age-out-of-range = возраст от { $min } до { $max }
account-no-such-line = такой линии нет
account-line-not-ready = эта линия ещё в разработке

## Счета (D-190). Их читает не только игрок: несходящаяся операция —
## это отказ движку, и он обязан назвать сумму и основание.

ledger-no-postings = операция без проводок
ledger-unbalanced = проводки не сходятся: сумма { $total }, основание { $reason }. Деньги переходят, а не появляются (И2)
ledger-not-positive = перевод должен быть положительным, получено { $amount }
ledger-insufficient = на счету столько нет: есть { $have }, требуется { $need }

## Отладочный виджет альфы (D-229)

alpha-no-such-thing = такой вещи в этом мире нет: { $goods }
alpha-amount-not-positive = количество должно быть больше нуля
alpha-amount-too-big = столько не бывает: не больше { $limit }
alpha-quality-out-of-range = качество — от { $min } до { $max }
alpha-liquid-nowhere = «{ NAME($goods) }» — жидкость, и налить её некуда: возьмите канистру в руки или встаньте там, где стоит бак. В ладонях жидкость не живёт
alpha-not-your-body = это тело не этой личности
alpha-nowhere = печатают в руки или на пол, а не «{ $where }»
alpha-no-grid = городской сети здесь нет: энергию печатают в пул, стоя в застройке города
