# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Что говорит командный слой: разбор запроса и поиск того, о чём он.
# Правила мира отказывают своими словами — эти в engine/*.ftl.
#
# Значение сообщения — одной строкой: перенос в исходнике Fluent попадает
# в текст отказа. Варианты выбора { $x -> ... } — каждый на своей строке,
# и эти переносы в текст не попадают.

## Кто спрашивает

cmd-no-live-body = нет живого тела
cmd-no-live-body-name-city = нет живого тела: назовите город явно
cmd-identity-gone = личность исчезла
cmd-identity-not-found = личность не найдена
cmd-body-off-node = тело вне узла
cmd-no-such-identity = нет такой личности
cmd-no-identity-named = нет личности «{ $name }»
cmd-person-not-here = такого человека здесь нет

## Учётка

cmd-old-password-wrong = старый пароль не подходит
cmd-password-wrong = пароль не подходит
cmd-passwords-differ = пароли не совпадают
cmd-email-taken = эта почта уже занята
cmd-account-without-identity = у аккаунта нет личности: регистрация не завершена

## Чего нет: спрошено то, чего не существует или не здесь

cmd-no-such-node = нет узла «{ $node }»
cmd-no-such-node-plain = нет такого узла
cmd-no-such-edge = нет такого ребра
cmd-no-such-thing = нет такой вещи
cmd-no-such-item = нет такого предмета
cmd-item-not-yours = этой вещи у вас нет
cmd-no-such-storage = нет такого хранилища
cmd-no-such-vessel = нет такой тары
cmd-no-such-vein = нет такой жилы
cmd-no-such-rig = нет такой установки
cmd-no-such-ship = нет такого корабля
cmd-no-such-plot = нет такой делянки
cmd-no-such-nursery = нет такого питомника
cmd-no-such-variety = нет такого сорта
cmd-no-such-deed = нет такой бумаги
cmd-no-such-order = нет такой заявки
cmd-no-such-reservation = нет такой брони
cmd-no-such-book-order = нет такого ордера
cmd-no-such-loan = нет такого займа
cmd-no-such-case = нет такого дела
cmd-no-such-office = нет такой должности
cmd-no-such-work-order = нет такого заказа
cmd-no-such-vote = нет такого голосования в этом городе
cmd-no-city-here = здесь нет города: за стенами законов нет

## Разбор запроса

cmd-not-your-job = задача не ваша
cmd-session-not-open = сессия не открыта
cmd-session-gone = сессия исчезла
cmd-need-hello = сначала hello
cmd-unknown-command = нет такой команды: { $cmd }
cmd-since-not-a-number = since должен быть числом
cmd-composition-shape = состав задаётся парами «вещь: сколько»
cmd-area-and-storeys-from-one = площадь и этажность считаются от единицы
cmd-need-layout = нужна раскладка: ключ узла — клетка
cmd-not-aboard = вы не на борту: назовите корабль или поднимитесь на него
cmd-nothing-to-resume = продолжать нечего: либо ничего не ждёт здесь, либо станция занята, либо работа уже идёт

## Печать первого тела (D-187, D-229)

cmd-door-does-not-print = у двери «{ $node }» не печатают
cmd-world-not-created = мир ещё не создан: печататься негде
