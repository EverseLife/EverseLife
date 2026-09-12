# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Разведка (D-321): ландшафт говорит, куда и как далеко можно, а не кубик.

## Карта-предмет (D-319 п. 6): память забывает, карта запечатляет
map-sheet-dead-draws = мёртвое тело не рисует
map-sheet-not-a-sheet = это не лист карты: рисуют на чистом листе
map-sheet-drawn = на листе уже рисовали: карта держит свои места, пока цела
map-sheet-empty = в памяти нет мест: рисовать нечего
map-sheet-no-strength = на рисование нужно { NUMBER($need, maximumFractionDigits: 0) } выносливости, а есть { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: память бесплатна, но работа — нет

explore-not-from-here = разведывают с земли планеты: не с борта и не из помещения
explore-too-near = слишком близко: до точки { $metres } м, а отсюда разведывают не ближе { $near } м
explore-too-far = слишком далеко: до точки { $metres } м, а отсюда разведывают не дальше { $far } м
explore-not-land = там вода или край света: узлу не на чем стоять
explore-into-water = прямой путь туда лежит через воду: с берега в море не разведывают, реку переходят бродом
explore-no-room = там тесно: земля уже занята — { $node }
explore-crosses-way = путь туда пересёк бы уже проложенный: разведывают между путей, а не поперёк них
explore-through-node = путь туда прошёл бы сквозь узел «{ $node }»: пути ведут мимо узлов, а не сквозь них
explore-already-out = разведка уже идёт
explore-harnessed = в упряжке не разведывают: по бездорожью повозка не пройдёт — распрягите её
explore-shut = там уже стоит { $node }, и дверь туда для вас закрыта
explore-not-out = разведка не идёт: возвращаться неоткуда
explore-already-joined = туда уже есть путь: { $node }
explore-scout-gone = разведчика нет на месте: поход пропал
explore-run-dangling = задание { $job }: разведчика или исходного узла больше нет
