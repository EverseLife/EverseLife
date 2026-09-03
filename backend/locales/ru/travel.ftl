# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Дорога: переходы, обоз, полотно, корабли и занятость тела
# (D-045, D-107, D-147, D-199, D-201, D-202, D-211, D-230, D-235, D-245).
#
# NAME($id) превращает устойчивый ключ вольта в слово этого языка (D-251):
# по проводу едет `roadbed`, читатель видит «Полотно». Имена узлов, кораблей
# и людей — уже слова, они едут простым { $arg }.
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.

# --- переход (D-045, D-091, D-152, D-199) -------------------------------------

travel-asleep = тело спит: сначала проснуться
travel-in-transit = тело в пути: { $left } — материя требует присутствия
travel-in-field = тело в разведке: { $left }; отменить заход — «вернуться» на карте
travel-no-route = { $how ->
        [convoy] обозу туда дороги нет: бездорожье транспорт не пускает
       *[foot] пути нет вовсе: узлы не связаны рёбрами
    }
travel-dead-goes-nowhere = мёртвое тело никуда не идёт
travel-already-going = тело уже в пути
travel-same-node = это тот же узел
travel-imprisoned = заключение: выходить из узла запрещено { $term ->
        [date] { $left }
       *[verdict] до решения суда
    }
travel-in-default = долг не обслуживается: выходить из узла нельзя, пока не рассчитаетесь. Заплатить за вас вправе кто угодно
travel-route-node-gone = маршрут ведёт в исчезнувший узел
travel-impassable = «{ NAME($vehicle) }» здесь не пройдёт: { $surface ->
        [road] дорога
        [paved] мощёный тракт
       *[trail] бездорожье
    } транспорт не пускает. Распрягитесь либо ищите дорогу
travel-no-strength = на дорогу нужно { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } выносливости, а есть { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: сначала поесть или поспать
travel-not-going = тело не в пути: возвращаться неоткуда
travel-passage-not-turned = «{ $node }» — чужая закрытая локация, и вы идёте через неё проходом: с полпути тут не поворачивают, проход идётся до конца
travel-job-no-leg = задание { $job }: перехода нет
travel-leg-nowhere = переход { $leg } ссылается в никуда
travel-plan-node-gone = переход { $leg }: план ведёт в исчезнувший узел
travel-not-an-exit = «{ $node }» — не выход из города: за стену ведут только ворота и космодром, дорогу тянут от них
travel-edge-in-use = по переходу сейчас идут: трап из-под идущего не убирают. Дождитесь, пока дорога освободится

# --- обоз (D-107, D-157) ------------------------------------------------------

transport-unknown-capacity = вольт не знает грузоподъёмности «{ NAME($vehicle) }»: заведите его в transport.capacity и transport.speed_k
transport-harness-dead = мёртвое тело никуда не впрягается
transport-not-a-vehicle = «{ NAME($vehicle) }» — не транспорт: впрягаются в повозку
transport-body-off-node = тело вне узла
transport-not-here = транспорта нет в этом узле: впрягаются в то, что рядом
transport-already-harnessed = уже впряжён: сначала распрячься
transport-vehicle-taken = в этот транспорт уже впряжены
transport-load-dead = мёртвое тело ничего не грузит
transport-load-not-harnessed = грузить некуда: сначала впрячься
transport-not-in-hands = этой вещи нет в руках: грузят своё и из рук
transport-nothing-to-load = грузить нечего
transport-overloaded = в трюме свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг, а это { NUMBER($mass, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг: больше грузоподъёмности не увезёт никто
transport-unload-dead = мёртвое тело ничего не выгружает
transport-unload-not-harnessed = выгружать нечего: сначала впрячься
transport-not-in-hold = этой вещи нет в трюме
transport-nothing-to-unload = выгружать нечего

# --- полотно (D-107) ----------------------------------------------------------

road-top-surface = мощёный тракт — верх лестницы: выше класть нечего
road-dead = мёртвое тело дорог не кладёт
road-stand-at-an-end = дорогу кладут стоя в одном из концов ребра
road-intact = дорога цела: подсыпать нечего
road-trail-not-mended = бездорожью подсыпать нечего: сначала уложить дорогу
road-edge-busy = на этом ребре уже идёт работа: дождитесь конца
road-no-goods = нужно { NUMBER($need, maximumFractionDigits: 0) } «{ NAME($goods) }», а в руках { NUMBER($have, maximumFractionDigits: 0) }: дорога — это материалы, а не намерение
road-already-queued = работа уже поставлена
road-job-no-edge = задание { $job }: ребра нет

# --- закладка и постройка корабля (D-202, D-215) ------------------------------

ship-keel-dead = мёртвое тело кораблей не закладывает
ship-no-name = у корабля должно быть имя
ship-body-off-node = тело вне узла
ship-keel-at-spaceport = основание корабля закладывают на космодроме: причалить больше некуда
ship-keel-not-aboard = к борту новый корабль не закладывают: основание кладут на космодроме планеты, а борт расширяют изнутри
ship-extend-dead = мёртвое тело кораблей не строит
ship-extend-from-aboard = корабль расширяют с борта: встаньте в узел корабля. Первый узел закладывают на космодроме
ship-extend-not-yours = это чужой корабль: строят у себя
ship-no-foundation = нужна «{ NAME($goods) }», а её в руках нет: корабль — это материалы, а не намерение. Делается по рецепту: { NAMES($makes) }
ship-keel-already-queued = закладка уже поставлена
ship-keel-job-no-node = закладка { $job }: узла нет
ship-keel-job-no-ship = закладка { $job }: корабля нет
ship-no-group = у корабля нет группы

# --- консоль и приказ (D-230, D-242) ------------------------------------------

ship-no-spaceport = в «{ $port }» нет космодрома: { $why ->
        [land] садиться некуда
        [turn-back] возвращаться некуда, корабль дойдёт до цели рейса
       *[dock] причаливать не к чему
    }
ship-no-mooring-to-hull = к борту не причаливают: цель рейса — космодром
ship-beacon-dark = маяк «{ $port }» не светит: узел промёрз или верфь без энергии. Космодром работает, пока в его узле тепло и есть чем питать верфь — принести туда генерацию можно только пешком
ship-command-dead = мёртвое тело кораблём не управляет
ship-not-yours = это чужой корабль
ship-no-console-here = кораблём управляют от консоли: встаньте в отсек, где стоит «Консоль управления кораблём»
ship-command-from-aboard = кораблём управляют с борта или от «Наземной консоли управления»: поднимитесь на него либо встаньте к наземной консоли
ship-console-not-yours = консоль чужая: приказы отдают со своей. Поставьте «Наземную консоль управления» в своём здании
ship-deaf = невозможно управлять «{ $ship }»: на борту нет «Консоли управления кораблём»

# --- рейс (D-201, D-232, D-233, D-235, D-245) ---------------------------------

ship-in-flight = «{ $ship }» уже в пути: до конца перехода он приказов не берёт
ship-in-passage = корабль уже в рейсе{ $known ->
        [true] { " " }в «{ $goal }»
       *[false] {""}
    }: до конца перехода он приказов не берёт
ship-no-connector-or-port = у корабля нет коннектора или порта
ship-not-enough-thrust = тяги { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) } на килограмм при нужных { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) }: с такой массой корабль никуда не идёт. Ставьте двигатели или снимайте груз
ship-no-life-support = на борту нет системы жизнеобеспечения: без неё корабль никуда не идёт
ship-no-engines = на корабле нет ни одного двигателя
ship-no-fuel = { $why ->
        [climb] на подъём топлива не хватает
        [cross] на уход со стоянки топлива не хватает
        [turn-back] на разворот топлива не хватает
        [orbit] на выход на орбиту звезды топлива не хватает
       *[land] на посадку топлива не хватает
    }: нужно { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } «{ NAME($goods) }» в пересчёте на ракетное, а баки закрывают { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }
ship-passage-already-queued = рейс уже поставлен
ship-already-in-orbit = «{ $ship }» уже на околопланетной орбите: выше подниматься некуда
ship-planet-has-no-orbit = у планеты { PLANET($planet) } нет орбитального узла
ship-cross-from-orbit = «{ $ship }» стоит в космодроме: между планетами ходят с орбиты. Сначала поднимитесь на околопланетную орбиту
ship-cross-to-orbit = «{ $node }» — не орбита: переход идёт с околопланетной орбиты на околопланетную орбиту, а космодром выбирают уже над планетой
ship-already-over-planet = «{ $ship }» уже над этой планетой: отсюда садятся, а не идут переходом
ship-nowhere-to-land = на «{ $node }» садиться некуда: не светит ни один маяк. Корабль ушёл бы туда и остался на орбите
ship-no-such-route = маршрута { PLANET($planet_from) } — { PLANET($planet_to) } в мире нет
ship-lost = корабль «{ $ship }» потерян: ни приказа, ни разворота ему больше не отдать
ship-no-route-adrift = из дрейфа небо дуги не даёт · { PLANET($planet_to) }
# Встреча двух корпусов (D-289, волна 3).
ship-target-self = корабль не летит сам к себе
ship-dock-self = корабль не стыкуется сам с собой
ship-target-unseen = цели не видно: чужой корпус виден ближе { NUMBER($radius) } ед. карты или на стоянке у той же планеты
ship-target-not-adrift = целью бывает только дрейфующий корпус: под приказом, на стоянке или на удержании его не встретить
ship-target-unknown = инерцию цели ещё не сочли: она только легла в дрейф, небо покажет её через минуту
ship-not-held = стыкуются только на удержании: подойти ближе { NUMBER($radius) } ед. карты с относительной скоростью ниже { NUMBER($speed) } ед. скорости
ship-dock-at-port = борт к борту стыкуются только в космосе: у причала это был бы мост мимо досмотра
ship-already-docked-ship = корабль уже пристыкован · { $other }
ship-not-docked-ship = корабль не пристыкован к другому корпусу
ship-no-route-to-ship = небо не даёт дуги к цели · { $other }
ship-no-course-to-cancel = курса нет — отменять нечего · { $ship }
ship-already-circling = корабль уже выходит на орбиту звезды · { $ship }
ship-orbit-only-in-space = на орбиту звезды выходят из космоса, а корабль у планеты — на стоянке или у причала · { $ship }
ship-course-not-turned = курс под небом не разворачивают: его отменяют или выходят на орбиту звезды · { $ship }
ship-orbit-crosses-planet = круг вокруг звезды отсюда проходит сквозь планету · { PLANET($body) }
ship-target-gone-by-then = цели к часу прихода там уже не будет: она упадёт или уйдёт из системы раньше · { $other }
ship-already-landed = «{ $ship }» уже стоит на планете: садиться неоткуда
ship-land-not-into-orbit = «{ $node }» — орбита, а не космодром: с орбиты садятся на планету под ней
ship-land-other-planet = «{ $node }» на другой планете: с орбиты садятся на то, что под ней, а до чужой планеты идут переходом с орбиты на орбиту
ship-not-in-passage = корабль никуда не идёт: разворачивать нечего
ship-already-turning-back = «{ $ship }» уже возвращается: разворачивать разворот некуда, дождитесь прихода
ship-no-home-to-turn-to = неизвестно, откуда «{ $ship }» ушёл: развернуться не к чему, и рейс придётся довести до конца
ship-turn-back-already-queued = разворот уже поставлен
ship-passage-nowhere = рейс { $job } ведёт в никуда
ship-no-connector = у корабля нет коннектора
ship-no-thrust-at-all = тяги нет вовсе

# --- чертёж корабля (D-178, D-202) --------------------------------------------

ship-arrange-dead = мёртвое тело кораблей не переустраивает
ship-arrange-from-aboard = корабль переустраивают с борта: поднимитесь на него
ship-name-too-long = имя длиннее { $limit } знаков
ship-cell-whole-number = клетка задаётся целым числом
ship-cell-not-fractional = отсек встаёт в клетку целиком: дробных клеток нет
ship-cell-off-the-grid = клетка { NUMBER($cell, useGrouping: 0) } за пределами чертежа: не дальше { NUMBER($reach, useGrouping: 0) } от начала
ship-nothing-to-arrange = нечего переставлять
ship-no-such-node = узла «{ $node }» на этом корабле нет
ship-cell-is-a-pair = клетка это пара чисел
ship-cell-is-a-pair-of-two = клетка это пара чисел: по горизонтали и по вертикали
ship-cell-taken = в одной клетке два отсека: «{ $first }» и «{ $second }»

# --- занятость тела (D-211) ---------------------------------------------------

occupation-busy = тело занято: { $what }{ $term ->
        [true] { " " }({ $left })
       *[false] {""}
    }

# The arc between worlds (D-271).
ship-hours-out-of-range = { NUMBER($hours) } ч — вне ползунка: дуга летит от часа до { NUMBER($limit) } ч
ship-no-arc = на { NUMBER($hours) } ч небо дуги не даёт: всякая срезает корону звезды. Выберите другое время на ползунке
ship-too-fast-for-thrust = за { NUMBER($hours) } ч двигатели выдают { NUMBER($have) } ед. скорости, а дуге нужно { NUMBER($need) }: сдвиньте ползунок к дешёвому краю, снимите массу или ставьте двигатели
ship-hours-is-a-number = время полёта задаётся числом часов
