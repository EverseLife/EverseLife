# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Хранение: руки, земля, пол, сундук, тара (D-181, D-230, D-244).
#
# NAME($id) превращает устойчивый ключ вещи в слово этого языка (D-251):
# по проводу едет `iron_ore`, читатель видит «Железная руда».
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.

storage-not-in-hands = этой вещи нет в руках: кладут своё и из рук
storage-nothing-to-put = класть нечего
storage-nothing-to-take = забирать нечего
storage-nothing-to-pick = поднимать нечего
storage-nothing-to-hand = передавать нечего
storage-not-in-storage = этой вещи нет в хранилище
storage-not-on-ground = этой вещи здесь не лежит
storage-not-in-hands-to-hand = этой вещи у вас в руках нет

storage-mismatch = «{ NAME($goods) }» в «{ NAME($chest) }» не кладут: { $why ->
        [vessel] тара берёт только жидкость
       *[chest] жидкость держат в таре
    }
storage-chest-full = в «{ NAME($chest) }» свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг, а это { NUMBER($mass, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг
storage-not-a-storage = «{ NAME($chest) }» — не хранилище: в него не кладут
storage-relic = «{ NAME($goods) }» — наследие Предтеч: его не поднимают и не уносят
storage-station-fuel = «{ NAME($goods) }» у станции — это её топливо: залитое обратно не поднимают

storage-no-building = здесь нет здания: класть можно только на землю
storage-storey-not-yard = это этаж, а не двор: под ним пол, а не земля
storage-no-room = { $inside ->
        [true] в здании
       *[false] на земле
    } свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } м², а под это нужно { NUMBER($needed, minimumFractionDigits: 1, maximumFractionDigits: 1) } м². Стройте больше, ставьте сундуки либо увозите

storage-passing-through = «{ $node }» — чужая закрытая локация, вы здесь проходом: проходом не берут и не кладут
storage-not-yours = хранилище не ваше: в чужой сундук не лезут. Открыть его вправе хозяин узла, а на городской земле — власть

storage-dead-puts = мёртвое тело ничего не кладёт
storage-dead-picks = мёртвое тело ничего не поднимает
storage-dead-hands = мёртвое тело ничего не передаёт
storage-dead-moves = мёртвое тело ничего не перекладывает
storage-hands-only = кладут из рук
storage-body-off-node = тело вне узла
storage-storage-not-here = этого хранилища здесь нет
storage-person-not-here = этого человека здесь нет
storage-dead-receives = мёртвому не передают
storage-self-hand = себе передавать нечего
