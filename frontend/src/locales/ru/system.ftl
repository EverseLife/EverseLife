# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова, которые говорит не панель, а сам клиент: связь с сервером, словарь
# планет и служебное окно альфы (D-251, волна IV).
#
# Здесь живёт то, у чьего текста нет своего окна. Отказ транспорта — «сервер
# не отвечает» — всплывает в полосе отказа любой панели: его произносит
# `api`, а показывает та, где нажали. Слово застроенного слоя принадлежит
# планете, а не карте, хотя читают его карта, обзор и меню узла. Виджет альфы
# уйдёт целиком одним швом, и его слова уйдут вместе с ним.
#
# Чего здесь нет и не будет — имён предметов, качеств, зданий и планет: их
# держит таблица переименований вольта, и берутся они функциями `NAME`,
# `KIND`, `PLANET`. Второй список тех же имён разошёлся бы с первым молча.
#
# Правила те же, что у `ui.ftl`: значение — одной строкой (перенос попал бы в
# текст); варианты выбора — каждый на своей строке.

## Разрыв связи: что `api` кладёт в отказ, когда сервера не слышно.
#
# Префикс `ui-wire-`, а не `ui-net-`: «сетью» в этом мире зовётся соцсеть
# (`Net.tsx`, `talk.ftl`), и одно слово на провод и на неё путало бы оба.

ui-wire-no-answer = сервер не отвечает
ui-wire-session-closed = сессия закрыта
ui-wire-no-session = нет сессии
ui-wire-timed-out = сервер не ответил

## Широкие права в городе (D-155). Узкое право зовётся именем своего закона.

ui-power-laws = все законы
ui-power-charter = устав
ui-power-treasury = казна
ui-power-offices = должности
ui-power-land = участки
ui-power-dashboard = панель города
ui-power-justice = суд
ui-power-citizens = граждане
ui-power-channel = канал города

## Как зовут застроенный слой на каждой планете (D-230).
#
# Имя самой планеты сюда не переезжает: его знает таблица переименований, и
# `PLANET($planet)` берёт его оттуда — на любом языке и без второго списка.

ui-planet-name = { PLANET($planet) }

ui-city-word-terra = город
ui-city-word-terra-in = в городе
ui-city-word-aquatica = коммуна
ui-city-word-aquatica-in = в коммуне
ui-city-word-pyroxis = лагерь
ui-city-word-pyroxis-in = в лагере
ui-city-word-aurora = заброшенный город
ui-city-word-aurora-in = в заброшенном городе

## Служебное окно альфы (D-229): печать вещей и досрочный срок.

ui-alpha-name = Альфа
ui-alpha-open-title = служебное окно альфы: печать вещей и досрочное завершение сроков
ui-alpha-fold = свернуть
ui-alpha-what = что напечатать
# Пример в пустом поле — имя из каталога, а не переписанное здесь заново.
ui-alpha-what-hint = { NAME("iron_ore") }
ui-alpha-amount = сколько
ui-alpha-quality = качество
ui-alpha-no-quality = без качества
ui-alpha-where = куда
ui-alpha-where-hands = в руки
ui-alpha-where-floor = на пол
ui-alpha-energy = энергии в пул города
ui-alpha-energy-hint = сколько
ui-alpha-energize = В пул
ui-alpha-energized = в пуле теперь: { $stored }
ui-alpha-print = Напечатать
ui-alpha-finish = Завершить сейчас
ui-alpha-printed = напечатано: { $goods } · { $amount }
ui-alpha-hurry-nothing = нечего ускорять: ничего не идёт
ui-alpha-hurried = срок подтянут: { $kinds }
ui-alpha-note-print = Печатается в руки или на пол и в журнал: у вещи записано основание «alpha», и найти всё, что мир не заработал, можно по нему. Энергия печатается в пул города, в котором вы стоите.
ui-alpha-note-hurry = «Завершить сейчас» двигает срок того, что вы уже начали, — разведки, перехода, работы, стройки, вспашки, перелёта и печати тела: доделывает их обычный обработчик, тот же, что и при честном ожидании.

## Виды сроков, которые альфа умеет подтянуть.

ui-alpha-job-explore-survey = разведка
ui-alpha-job-travel-leg = переход
ui-alpha-job-craft-batch = работа
ui-alpha-job-ship-keel = закладка корабля
ui-alpha-job-ship-flight = перелёт
ui-alpha-job-build-finish = стройка
ui-alpha-job-build-demolish = снос
ui-alpha-job-build-repair = ремонт
ui-alpha-job-farm-plow = вспашка
ui-alpha-job-body-print = печать тела

## Городская управа: то, что осталось от прохода по панелям.

ui-admin-lot-area = { $area } м²
