# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Разведка (D-321): ландшафт говорит, куда и как далеко можно, а не кубик.

explore-not-from-here = разведывают с земли планеты: не с борта и не из помещения
explore-too-near = слишком близко: до точки { $metres } м, а отсюда разведывают не ближе { $near } м
explore-too-far = слишком далеко: до точки { $metres } м, а отсюда разведывают не дальше { $far } м
explore-not-land = там вода или край света: узлу не на чем стоять
explore-into-water = прямой путь туда лежит через воду: с берега в море не разведывают, реку переходят бродом
explore-no-room = там тесно: земля уже занята — { $node }
explore-crosses-way = путь туда пересёк бы уже проложенный: разведывают между путей, а не поперёк них
explore-already-out = разведка уже идёт
explore-already-joined = туда уже есть путь: { $node }
explore-scout-gone = разведчика нет на месте: поход пропал
explore-run-dangling = задание { $job }: разведчика или исходного узла больше нет
