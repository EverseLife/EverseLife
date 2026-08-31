# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова общих модулей: часы, оси сортировки инвентаря, налив в таре, выбор
# качества, гербы участка (D-251, волна IV).
#
# Эти слова не принадлежат ни одной панели: их произносят `clock`, `arrange`,
# `liquids`, `Tier` и `marks` — а показывают полдюжины окон сразу. Поэтому и
# файл свой: панель, которая их читает, не владеет ими.
#
# Правила те же, что у `ui.ftl`: значение — одной строкой, каким бы длинным
# оно ни было (перенос попал бы в текст); варианты выбора — каждый на своей
# строке, эти переносы в текст не попадают.

## Часы: местное время планеты и то, как далеко отстоит момент.

ui-clock-stamp = сутки { $day } · { $hands }
ui-clock-never = —
ui-clock-soon = вот-вот
ui-clock-just-now = только что
ui-clock-ahead = через { $span }
ui-clock-ago = { $span } назад

## Срок словами. Сокращения единиц по-русски не склоняются.

ui-clock-seconds = { $n } с
ui-clock-minutes = { $n } мин
ui-clock-hours = { $n } ч
ui-clock-hours-minutes = { $n } ч { $rest } мин
ui-clock-days = { $n } сут

## Оси, по которым игрок раскладывает инвентарь.

ui-arrange-group-none = без групп
ui-arrange-group-goods = по предмету
ui-arrange-group-tier = по качеству
ui-arrange-group-kind = по типу
ui-arrange-group-maker = по клейму

ui-arrange-sort-name = по названию
ui-arrange-sort-quality = по качеству
ui-arrange-sort-amount = по количеству
ui-arrange-sort-mass = по массе
ui-arrange-sort-condition = по состоянию
ui-arrange-sort-spoils = по годности

## Заголовок группы: чем стопка оказалась, и чего у неё нет.

ui-arrange-kind-carriers = носители
ui-arrange-kind-coins = монеты
ui-arrange-kind-raw = сырьё
ui-arrange-kind-food = еда
ui-arrange-kind-station = рабочие станции
ui-arrange-kind-furniture = мебель
ui-arrange-kind-tool = инструменты
ui-arrange-kind-gear = снаряжение
ui-arrange-kind-vehicle = транспорт
ui-arrange-kind-material = материалы
ui-arrange-kind-consumable = расходники
ui-arrange-kind-other = прочее
ui-arrange-no-tier = без качества
ui-arrange-no-maker = без клейма

## Налив в таре (D-230).

ui-liquid-empty = пусто
ui-liquid-fill = { $what } · { $mass } из { $capacity } кг

## Выбор качества: что пустить в дело.

ui-tier-none = качество: в руках нет
ui-tier-none-title = «{ $goods }» в руках нет
ui-tier-any = качество: любое (худшее первым)
ui-tier-title = какое качество «{ $goods }» пустить в дело
# Строка одной ступени в списке: сколько её в руках и в каком разбросе качества.
ui-tier-stock = { $tier } · { $amount } · кач. { $span }

## Гербы, которые хозяин прибивает на участок (D-238).

ui-emblem-house = дом
ui-emblem-field = поле
ui-emblem-woods = лес
ui-emblem-meadow = луг
ui-emblem-stones = камни
ui-emblem-workshop = мастерская
ui-emblem-market = рынок
ui-emblem-warehouse = склад
ui-emblem-food = еда
ui-emblem-water = вода
ui-emblem-markup = разметка
