# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Мастерская: партия, чеканка, станции, библиотека, снаряжение
# (D-092, D-133, D-016, D-106, D-053, D-146).
#
# NAME($id) превращает устойчивый ключ вещи, класса, операции или свойства
# узла в слово этого языка (D-251): по проводу едет `iron_ore`, читатель
# видит «Железная руда».
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.

# --- крафт: партия и её условия (D-092, D-133) -------------------------------

craft-dead-works = мёртвое тело не работает
craft-dead-cooks = мёртвое тело не готовит
craft-dead-reads = мёртвое тело не читает
craft-dead-wipes = мёртвое тело ничего не стирает
craft-body-off-node = тело вне узла
craft-body-without-identity = тело без личности

craft-zero-batch = партия из нуля единиц
craft-batch-too-big = партия больше craft.batch_max: { $units }
craft-counted-whole = «{ NAME($goods) }» считается штуками: партия из целых единиц
craft-not-learned = рецепт «{ NAME($recipe) }» не скопирован в личность
craft-not-enough = не хватает «{ NAME($goods) }»: нужно ещё { $short }
craft-item-not-in-hands = вещь не в руках: чинят и разбирают своё, а не чужое

craft-no-place = здесь нет: { NAME($place) }
craft-place-not-yours = { NAME($place) } на чужой земле: рубить может хозяин

craft-no-station = в узле нет рабочей станции «{ NAME($station) }»
craft-station-busy = { $whose ->
        [own] «{ NAME($station) }» занята вашей же работой: дождитесь конца партии
       *[other] «{ NAME($station) }» занята: за рабочей станцией работает один. Свою ставят у себя
    }
craft-cut-off = «{ $node }» отключён за неуплату: рабочие станции не работают, пока долг не закрыт

craft-no-tool = нужен инструмент: { NAME($tool) }
craft-tool-not-in-hands = этого инструмента нет в руках: tool — вещь из твоей сумки, а станок в узле берётся сам

# --- носители знания (D-209, D-215) ------------------------------------------

craft-write-needs-recipe = на носитель записывают конкретный рецепт: назовите какой
craft-write-not-learned = рецепт «{ NAME($recipe) }» не в личности: записать можно только своё
craft-not-a-carrier = «{ NAME($goods) }» — не носитель: рецепт на него не записывают
craft-carrier-not-in-hands = носителя нет в руках
craft-carrier-blank = это не записанный носитель: читать нечего
craft-wipe-not-a-carrier = стереть можно только носитель знания
craft-no-blank = у «{ NAME($carrier) }» нет болванки: класс «{ NAME($cls) }» пуст
craft-blank-dead = { $live ->
        [true] болванка стёрта в ноль: на неё уже ничего не записать (живых не хватает)
       *[false] болванка стёрта в ноль: на неё уже ничего не записать
    }

# --- котёл (D-119, D-128, 16-cooking) ----------------------------------------

craft-not-a-dish = «{ NAME($goods) }» — не блюдо: это делают партией, не котлом
craft-is-a-dish = «{ NAME($goods) }» — блюдо: его варят котлом, командой `cook`
craft-unknown-roles = нет таких ролей: { $roles }
craft-not-ingredient = «{ NAME($goods) }» — не продукт: в котёл кладут съедобное
craft-empty-pot = в котле пусто: закройте хотя бы одну роль

# --- способ изготовления -----------------------------------------------------

craft-unknown-way = { $known ->
        [true] «{ NAME($goods) }» не делается способом «{ $way }»; способы: { $ways }
       *[false] «{ NAME($goods) }» не делается способом «{ $way }»
    }
craft-unmakeable = «{ NAME($goods) }» не делается ни по рецепту, ни операцией
craft-is-a-coin = «{ NAME($goods) }» — монета: её чеканят, и металл считается по пробе (команда `coin.mint`)
craft-operation-extracts = операция «{ NAME($operation) }» ничего не расходует: это добыча, а не крафт
craft-coin-melts-elsewhere = монету переплавляют командой `coin.melt`: металл возвращается по её пробе, а не по норме рецепта

# --- изобретение (D-092) -----------------------------------------------------

craft-empty-composition = состав пуст: положите хоть что-нибудь
craft-too-many-ingredients = в один состав кладут не больше { NUMBER($max, maximumFractionDigits: 0) } видов вещей
craft-known-operation = это «{ NAME($operation) }» — операция без рецепта, она и так в списке
craft-already-known = «{ NAME($recipe) }» вы уже знаете: выберите его из списка
craft-invent-failed = Состав не сложился: часть выложенного сгорела. Подсказок нет — думайте и пробуйте

# --- библиотека как источник знания (D-053, D-068, D-148) --------------------

craft-no-library = Библиотека не работает удалённо: за знанием надо прийти
craft-library-lacks = в этой библиотеке нет «{ NAME($recipe) }»: его сюда ещё не принесли
craft-no-strength = на переписывание нужно { NUMBER($need, maximumFractionDigits: 0) } выносливости, а есть { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: знание бесплатно, но работа — нет

# --- внутренние сбои: партия без задания и наоборот ---------------------------

craft-job-without-batch = задание { $job }: партии нет
craft-batch-dangling = партия { $batch } ссылается в никуда
craft-target-gone = работа { $batch }: вещи больше нет

# --- монета: чеканка и переплавка (D-016, D-086) -----------------------------

coin-dead-mints = мёртвое тело не чеканит
coin-dead-works = мёртвое тело не работает
coin-not-a-coin = «{ NAME($goods) }» — не монета
coin-not-minted = «{ NAME($goods) }» — не монета: её делают партией, не чеканкой
coin-not-melted = «{ NAME($goods) }» — не монета: это переработка, а не переплавка
coin-no-composition = у «{ NAME($goods) }» не задан состав: чеканить не из чего
coin-no-input = у «{ NAME($goods) }» нет входа: чеканить не из чего
coin-whole-only = монеты считаются целыми штуками
coin-not-in-hands = монета не в руках: плавят своё
coin-not-enough = столько монет нет: в стопке { $have }

# --- рабочие станции и мебель (D-106, D-150, D-181, D-232) -------------------

station-dead-places = мёртвое тело ничего не ставит
station-dead-takes = мёртвое тело ничего не уносит
station-body-off-node = тело вне узла
station-not-in-hands = этой вещи нет в руках
station-not-in-node = этой вещи нет в этом узле
station-not-placeable = «{ NAME($goods) }» — не рабочая станция и не мебель: в здание ставят оборудование
station-not-a-station = «{ NAME($goods) }» — не рабочая станция и не мебель
station-built-in-place = { NAME($goods) }: строится на месте и в руки не берётся
station-relic = «{ NAME($goods) }» — наследие Предтеч: не снимается и не разбирается
station-node-not-yours = узел не ваш: оборудование ставят у себя. Пустой городской участок выкупают, дикий — занимают
station-take-not-yours = узел не ваш: чужое оборудование не уносят
station-busy = за рабочей станцией работают: дождитесь конца партии
station-not-empty = в «{ NAME($chest) }» лежат вещи: сначала разберите, потом уносите
station-no-building = на участке нет здания: сначала строят, потом обставляют
station-no-room = { $slots ->
        [one] в здании { $slots } место по { $per } м², и все заняты: стройте больше либо уносите лишнее
        [few] в здании { $slots } места по { $per } м², и все заняты: стройте больше либо уносите лишнее
       *[many] в здании { $slots } мест по { $per } м², и все заняты: стройте больше либо уносите лишнее
    }

# --- библиотека как хранилище рецептов (D-053, D-068) ------------------------

library-dead-brings = мёртвое тело ничего не приносит
library-not-here = Библиотеки здесь нет: рецепт приносят в неё ногами
library-not-in-hands = этой вещи нет в руках
library-not-a-carrier = в библиотеку кладут записанный носитель — предмет «Рецепт»
library-already-there = «{ NAME($recipe) }» в этой библиотеке уже есть: носитель остаётся у вас

# --- ноша и снаряжение (D-146, D-129) ----------------------------------------

gear-dead-dresses = мёртвое тело не одевается
gear-not-in-hands = вещь не в руках: надевают своё
gear-no-slot = «{ NAME($goods) }» не надевается: у него нет слота
gear-unknown-slot = слота «{ $slot }» в мире нет
gear-overloaded = не унести: в руках { NUMBER($carries, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг из { NUMBER($limit, maximumFractionDigits: 0) }, а это ещё { NUMBER($extra, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг. Всё сверх — только транспортом
craft-unpowered-no-grid = станок «{ NAME($goods) }» работает от сети, а сети здесь нет: вне города рядом ставят заряженный аккумулятор
craft-unpowered-short = станку «{ NAME($goods) }» нужно { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 1) } энергии, а в пуле { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 1) }: город без топлива стоит
craft-unpowered-cells = станку «{ NAME($goods) }» нужно { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 1) } энергии, а в аккумуляторах рядом { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 1) }
