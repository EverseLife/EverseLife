# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова окон о мире: карта, корабль, забой, буровая, огород, станция,
# питомник, собирательство (D-251, волна IV).
#
# Живут здесь по той же причине, что и `ui.ftl`: это голос интерфейса, он
# меняется вместе с той версией клиента, которая его показывает, и попадает
# в сборку, а не в сеть.
#
# Значение — одной строкой (перенос попал бы в текст); варианты выбора —
# каждый на своей строке, эти переносы в текст не попадают. Ключ варианта —
# идентификатор, поэтому «есть ли имя» приходит флагом `true`/`false`, а не
# строкой.
#
# Числа приходят уже строками: `12`, `1.5`, `<0.1`. Fluent сам оформил бы
# число по языку — «1,5» вместо «1.5» — и колонка чисел разошлась бы с той,
# что рядом собрана в коде.

## Карта: высота, узлы, дороги, небо.

ui-map-inside = Внутрь
ui-map-outside = Наружу

## Где стоит узел: игрок читает место, а не enum.

ui-map-where-space = в космосе
ui-map-where-planet = на планете
ui-map-where-location = внутри места

## Срок дороги и цена телу: единицы читаются взглядом, а не сравнением.

ui-map-unit-minutes = мин
ui-map-unit-hours = ч
ui-map-term-hours = { $term } ч
ui-map-term-days = { $term } сут

## Столбец рядом с картой: всё об узле, который выбрали.

ui-map-ongoing = В пути
ui-map-ongoing-rule = Пока идёшь, тебя нет нигде: добыча, крафт, погрузка и покупка закрыты, а счёт и ордера работают. Повернуть назад можно в любой момент — вернёшься туда, откуда вышел, а потраченное не вернётся.
ui-map-ongoing-leg = сейчас — отрезок до «{ $to }»
ui-map-ongoing-direct = прямой переход
ui-map-ongoing-left = впереди ещё { $count } узл.
ui-map-transit-label = переход
ui-map-turn-back = Повернуть назад
ui-map-scouting = Разведка
ui-map-scouting-rule = Разведчик идёт к точке, которую вы указали, и по сроку окажется на ней: поход — это ходьба в одну сторону. Пока он идёт, тела нет на месте. Отменить можно в любой момент — вернётесь туда, откуда вышли, а потраченное не вернётся.
ui-map-scouting-far = до точки { $metres } м
ui-map-scouting-away = точка на другой планете
ui-map-scouting-label = разведка
ui-map-scouting-stop = Отменить разведку
ui-map-here = Вы здесь
ui-map-node-unnamed = Безымянный узел
ui-map-enter = Войти
ui-map-node-rule = Идти можно в любой узел на карте: маршрут строится сам по времени с учётом покрытия, каждый отрезок — отдельное задание, и приход сам выводит в следующий. По прямой не ходят: нет ребра — нет пути. Карта показывает два шага вокруг вас — дальний узел откроется, когда вы к нему приблизитесь.
ui-map-node-ship-flight = корабль · в рейсе
ui-map-node-ship-port = корабль · у космодрома
ui-map-node-expandable = есть что раскрыть
ui-map-node-far = другая планета: смотреть отсюда нечего
ui-map-flight-label = рейс
ui-map-road = дорога
ui-map-road-price = стоит тела
ui-map-planet-deferred = Планета вне альфы: её ещё нет в мире, и попасть на неё нельзя.
ui-map-planet-other = Другая планета. Пешком туда пути нет: только кораблём с космодрома.
ui-map-planet-ship = Планета вашего корабля.
ui-map-planet-own = Ваша планета: вы стоите на её поверхности.
ui-map-ship-flying = Корабль в рейсе: трапа нет, пока он не причалит.
ui-map-ship-gangway = На борт заходят ногами, по трапу с космодрома.
ui-map-node-offworld = Это другая планета: пешком туда пути нет, только кораблём с космодрома.
ui-map-node-far-walk = Соседним не является: маршрут построится сам, по проходимым рёбрам.
ui-map-go = Идти
ui-map-expand = Раскрыть

## Меню на узле под правой кнопкой.

ui-map-menu-here = Вы здесь.
ui-map-menu-walking = Пока идёшь, никуда не выйти.

## Подписи на самих узлах.

ui-map-node-alpha = вне альфы
ui-map-node-spaceport = космодром

## Дороги от узла: что уложено, что просело и чего это стоит.

ui-map-surface-wild = бездорожье
ui-map-surface-trail = тропинка
ui-map-surface-road = дорога
ui-map-surface-paved = тракт
ui-map-road-working = идёт работа
ui-map-road-need = нужно { $needs } полотна, в руках { $hand }
ui-map-road-lay = Проложить за { $needs }
ui-map-road-pave = Мостить за { $needs }
ui-map-road-mend-need = подсыпка: { $needs } полотна
ui-map-road-mend = Подсыпать за { $needs }
ui-map-road-at-hand = полотна в руках { $hand }
ui-map-road-rule = Покрытие поднимается на ступень за полотно и время: бездорожье → тропинка → дорога → мощёный тракт. Тропинку протаптывают ноги, и без ходьбы она зарастает; без содержания зарастает и дорога. Ни по бездорожью, ни по тропинке обоз не идёт. Под снегом бездорожье и тропинка дольше; дорогу и тракт расчищают.

## Небо: прокрутка времени и слой космоса.

ui-map-sky-stop = Остановить
ui-map-sky-wind = Прокрутить
ui-map-sky-slider = на сколько суток вперёд показано небо
ui-map-sky-now-note = сейчас
ui-map-sky-ahead = +{ $days } сут
ui-map-sky-now = Сейчас
# Промотка года планеты (D-334): сезон, снег и лёд на год вперёд.
ui-map-year-wind = Прокрутить год
ui-map-year-slider = на сколько суток вперёд показана планета
ui-map-year-pace = скорость прокрутки
ui-map-year-pace-sixteenth = ×¹⁄₁₆
ui-map-year-pace-eighth = ×⅛
ui-map-year-pace-quarter = ×¼
ui-map-year-pace-one = ×1
ui-map-year-pace-four = ×4
ui-map-year-pace-sixteen = ×16
ui-map-year-rule = Планета наклонена к своей орбите: за год солнце ходит по широте, а с ним приходят и уходят снег и лёд. Прокрутка показывает год вперёд — свет, тени и снег; сама планета от этого не меняется.
ui-map-sky-rule = Планеты идут вокруг звезды каждая своим сроком, и расстояние между ними меняется само. Глазом этого не видно: за час орбита проходит доли градуса — поэтому ход времени показывает прокрутка, а не ожидание.

## Полоса над картой: высота взгляда и привязка камеры.

ui-map-cam-tied = камера за вами
ui-map-cam-free = камера свободна
ui-map-zoom = приблизить или отдалить

## Слои карты (D-331): чем окрашена земля и что лежит поверх.

ui-map-layers = слои
ui-map-layers-now = слои: { $layer }
ui-map-layers-ground = окраска земли
ui-map-layers-over = поверх
ui-map-layers-over-terrain = видны на слое «{ ui-map-layer-terrain }»
ui-map-layer-terrain = местность
ui-map-layer-relief = рельеф
ui-map-layer-biomes = биомы
ui-map-layer-temperature = температура
ui-map-layer-rain = осадки
ui-map-layer-moisture = влажность почвы
ui-map-layer-weather = погода
ui-map-layer-provinces = провинции
ui-map-layer-city-lands = городские земли
ui-map-layer-contours = горизонтали
ui-map-layer-figures = растительность
ui-map-layer-clouds = облака
ui-map-degrees = { $c }°
ui-map-legend-rain-less = меньше дождей
ui-map-legend-rain-more = больше дождей
ui-map-legend-moisture-fast = сохнет быстро
ui-map-legend-moisture-slow = сохнет медленно
ui-map-legend-weather-dry = сухо
ui-map-legend-weather-heavy = ливень

## Само поле карты.

ui-map-loading = карта грузится…
ui-map-empty = Здесь пока ничего нет.
ui-map-node-drawn = по карте, рисованной в сутки { $day }: что здесь теперь — карта не знает
ui-map-world = карта мира

## Корабль: карточка корпуса, приказы рубки, чертёж.

ui-ship-title = Корабль
ui-ship-yard = Космическая верфь
ui-ship-console = Консоль управления кораблём
ui-ship-ground-console = Наземная консоль управления
ui-ship-console-aground = Консоль стоит на земле и молчит: она работает только в узле корабля — на основании, заложенном на космодроме из «узла космического корабля». Для приказов с земли есть другая вещь — «Наземная консоль управления».
ui-ship-rule = Корабль — не вещь, а группа узлов карты с одним выходом наружу. Швартовка и отход — появление и исчезновение одного ребра, а полёт это его отсутствие: с борта просто некуда сойти. Скорость выводится из тяги против массы, поэтому грузоподъёмности числом нет — перегруженный корабль остаётся в порту. Дорога идёт тремя ногами: подъём на околопланетную орбиту, переход с орбиты на орбиту, спуск на выбранный космодром. Курс задаётся на карте рубки: она показывает часы и топливо именно этого корпуса.

## Карточка корпуса: двигатели, масса, скорость, воздух.

ui-ship-engines = двигатели
ui-ship-engines-none = нет ни одного: корабль не летит
ui-ship-engine-row = ×{ $count } · тяга { $thrust } каждый · класс { $class }
ui-ship-mass = масса
ui-ship-mass-parts = корпус { $hull } кг · станции { $machines } кг · груз { $cargo } кг
ui-ship-speed = скорость
ui-ship-ratio = { $ratio } тяги на кг массы
ui-ship-class = класс { $class }
ui-ship-below-threshold = ниже порога отрыва
ui-ship-air = кислород
ui-ship-air-line = { $units } на линии жизнеобеспечения
ui-ship-air-burn = расход { $spend } в час · хватит на { $term }
ui-ship-air-covered = экипажа нет, расхода нет
ui-ship-air-outside = за бортом воздух, система спит

## Питание (D-288): линии от машины к таре.

ui-ship-feed = Питание
ui-ship-feed-hint = Порт без отметок ни из какой тары не берёт и ни в какую не льёт. Отметьте тару; порядок отметок — порядок, в каком порт берёт из неё или льёт в неё.
ui-ship-feed-reset = снять всю линию
ui-ship-feed-no-vessels = Подходящей установленной тары на борту нет: поставьте бак, канистру или баллон в отсек.
ui-ship-feed-empty = пусто
ui-ship-feed-up = выше
# Схема корабля (D-288, D-340): место правки линий, из рубки.
ui-ship-scheme = Схема корабля
ui-ship-scheme-rule = линии от машин к таре
ui-ship-scheme-hint = Дорожки — отсеки в порядке закладки: слева машины, справа тара. Линию ведут от точки порта к таре — перетаскиванием или щелчком по порту, а затем по таре. Щелчок по линии открывает её порт, щелчок по таре без выбранного порта — правку её имени.
ui-ship-scheme-read-only = Линии проводит и тару называет владелец корабля у консоли; экипаж видит схему как есть.
ui-ship-scheme-none = На борту нет машин с портами — тех, что берут или отдают жидкость по линиям: они появятся здесь, когда встанут в отсек.
ui-ship-scheme-way-in = вход
ui-ship-scheme-way-out = выход
ui-ship-scheme-way-vent = сброс
ui-ship-scheme-way-in-note = Берёт из тары по порядку линии: опустела первая — берёт из следующей.
ui-ship-scheme-way-out-note = Льёт в тару по порядку линии; когда полна вся — машина стоит.
ui-ship-scheme-way-vent-note = Льёт в тару по порядку линии. Что не вошло: кислород гидропоники остаётся в воздухе отсека, сбросной газ уходит за борт, где снаружи нет воздуха; под небом с воздухом сбросному газу место только в таре, и без неё машина стоит.
ui-ship-scheme-no-line = { $way ->
        [in] линии нет — порт ничего не берёт
        [vent] линии нет — кислород остаётся в воздухе отсека, сбросной газ в пустоте уходит за борт, а под небом с воздухом машина стоит
       *[other] линии нет — машине некуда лить, и она стоит
    }
ui-ship-scheme-port = порт «{ $goods }»: потяните к таре
ui-ship-scheme-line-pick = открыть порт этой линии
ui-ship-scheme-down = ниже
ui-ship-scheme-unline = снять
ui-ship-scheme-done = готово
ui-ship-scheme-name-label = Имя тары
ui-ship-scheme-name-clear = снять имя
ui-ship-scheme-stall-power = стоит: аккумуляторы корпуса сели
ui-ship-scheme-stall-dry = стоит: пусто на линии «{ $goods }»
ui-ship-scheme-stall-full = стоит: полна тара на линии «{ $goods }»
ui-ship-scheme-stall-unlined = стоит: нет линии «{ $goods }»

## Строка о корпусе, одна на каждый: где он и чем дышит.

ui-ship-sign = { $name } · { $nodes } узл. · тяга { $thrust } на массу { $mass } кг
ui-ship-ratio-line = тяговооружённость { $ratio } при нужных { $min }
ui-ship-stuck = не отрывается
ui-ship-crew = экипаж { $crew } · топлива { $fuel }
ui-ship-no-life-support = без системы жизнеобеспечения
ui-ship-in-orbit = на околопланетной орбите · { $planet }
ui-ship-berthed = у верфи «{ $port }», место { $berth }
ui-ship-on-voyage = в рейсе в «{ $name }»
ui-ship-adrift = в дрейфе
ui-ship-deaf = Невозможно управлять. На борту нет «Консоли управления кораблём».
ui-ship-console-borrowed = Консоль чужая: приказы отдают со своей. Поставьте в своём здании: «{ $console }».
ui-ship-no-bridge = Отстыковка и рейс отдаются от консоли управления: встаньте в отсек, где она стоит. Без консоли на борту корабль никуда не летит.

## Подъём: единственный ход корпуса на земле.

ui-ship-no-orbit = Отсюда не подняться: у этой планеты нет орбитального узла.
ui-ship-no-thrust = тяги нет вовсе: поставьте двигатель
ui-ship-leg-cost = { $hours } ч · { $fuel } топлива
ui-ship-ascend = Подняться на околопланетную орбиту
ui-ship-ascend-hint = подъём занимает время по тяжести планеты и тяге корпуса; его можно развернуть
ui-ship-thrust-short = тяги не хватает, чтобы оторваться: снимите массу или добавьте двигатель
ui-ship-ratio-short = Тяговооружённости не хватает: корабль не отрывается.
ui-ship-dry-climb = В баках { $fuel }, а на подъём нужно { $need }: с площадки корабль не уйдёт.
ui-ship-dry-ascent = В баках { $fuel }, а с подъёмом и спуском обратно нужно { $need }: корабль поднимется, но на орбите останется, пока не привезут топливо.
ui-ship-reserve = Сверх подъёма на спуск обратно уйдёт ещё { $kept } ед. топлива.
ui-ship-course-later = Курс на другую планету задаётся уже с орбиты: сперва подъём, потом переход, потом выбор космодрома над планетой.
ui-ship-airless-none = Кислорода на линии жизнеобеспечения нет, и ни одна установленная тара его не держит. Без воздуха за бортом экипажу дышать нечем. Поставьте баллон в отсек и проведите к нему линию — раздел «{ ui-ship-feed }».
ui-ship-airless-stowed = Кислорода на линии жизнеобеспечения нет. На борту, вне линии: { $off } ед. Без воздуха за бортом экипажу дышать нечем. Проведите линию к этой таре — раздел «{ ui-ship-feed }».

## Спуск: причал выбирают уже над планетой.

ui-ship-nowhere-to-land = Садиться здесь некуда: ни одного космодрома с горящим маяком на этой планете. Курс на другую планету задаётся на карте.
ui-ship-land-title = Сесть на планету
ui-ship-pad-choice = площадка для посадки
ui-ship-pad-wild = безымянный узел
ui-ship-pad-room = свободно { $room } м²
ui-ship-pad-full = места нет: свободно { $room } м², корпусу нужно { $need }
ui-ship-pads-label = планета под кораблём: площадки для посадки
ui-ship-pads-hint = поверните планету и выберите площадку: размер метки — её свободная земля, пустая метка корпус не вместит
ui-ship-land = Сесть
ui-ship-land-hint = спуск идёт по тяжести планеты и тяге корпуса — чуть дешевле подъёма
ui-ship-land-short = тяги не хватает даже на посадку: снимите массу

## Рейс: куда идёт, сколько осталось и можно ли повернуть.

ui-ship-flight = { $back ->
        [true] разворот
       *[false] рейс
    } в «{ $name }»
ui-ship-flight-label = рейс
ui-ship-flight-autopilot = Автопилот ведёт корабль сам: на каждом шаге заново прокладывает переход оттуда, где корабль сейчас, и баки платят по ходу. Курс менять нельзя.
ui-ship-may-cancel = Можно отменить курс или выйти на орбиту звезды.
ui-ship-cancel-course = Отменить курс
ui-ship-star-orbit = Выйти на астроцентрическую орбиту
ui-ship-star-orbit-hint = Двигатели гасят разницу между своей скоростью и скоростью круга вокруг звезды на текущем радиусе; баки платят по ходу. Дальше корабль висит на круге, как планета.
ui-ship-flight-star = выход на орбиту звезды
ui-ship-recall = Развернуться{ $known ->
        [true] { " " }в «{ $port }»
       *[false] {""}
    }
ui-ship-no-origin = Неизвестно, откуда корабль ушёл: развернуться не к чему, он дойдёт до конца.

## Курс: чего стоит выбранная на карте планета.

ui-ship-pick-planet = Курс задаётся на карте: выберите планету.
ui-ship-no-route = Отсюда туда хода нет: либо маршрута в мире не заведено, либо на той планете не светит ни один маяк — корабль ушёл бы туда и остался на орбите.
ui-ship-thrust-cut = тяги не хватает: снимите массу
ui-ship-fly = Лететь
ui-ship-fly-hint = переход идёт с орбиты на орбиту; космодром выбирается уже над планетой

## Имя корпуса: слово владельца, движок из него ничего не выводит.

ui-ship-rename = Переименовать
ui-ship-name-label = имя корабля
ui-ship-name-set = Назвать
ui-ship-cancel = Отмена

## Закладка: часы между списанной основой и появившимся узлом.

ui-ship-lay-keel = Заложить основание для космического корабля
ui-ship-keel-label = закладка
ui-ship-keel-note = основа списана, узел появится сам — стоять у верфи не нужно. Но руки заняты закладкой: до срока не выйдет ни спать, ни разведывать, ни встать к станции. Ходить можно.
ui-ship-name-placeholder = Имя корабля
ui-ship-foundation-word = основа узла корабля
ui-ship-need-foundation = Нужна «{ $goods }» в руках — её делают в космической мастерской. Корабль растёт по узлу за раз: каждый следующий узел это и место, и лишняя масса.

## Карта рубки и чертёж отсеков.

ui-ship-chart = карта рейса
ui-ship-plan = план корабля
ui-ship-plan-rule = Перетаскивайте отсеки по сетке: меняется только чертёж. Переходы остаются те, что возникли при закладке, и каждый из них — одна секунда. Пустое поле тянет чертёж, колесо приближает.
ui-ship-plan-askew = Часть отсеков стоит не по клеткам: их поставили до сетки.
ui-ship-plan-home = К основанию
ui-ship-plan-align = Выровнять по сетке

## Забой: три кнопки и рычаг темпа.

ui-mine-title = Забой
ui-mine-rule = Крепь стоит бруса и верёвки, быстрый темп даёт больше выхода и больше просадки. Заученной последовательности нет: оптимум двигается вместе с ценой крепи. Тело переживает первый обвал, но не второй.
ui-mine-vein = Жила: { $goods }, богатство { $richness }
ui-mine-no-vein = В этом узле жилы нет
ui-mine-no-vein-here = здесь нет жилы
ui-mine-computing = считаю плату устройства…
ui-mine-start = Начать сессию
ui-mine-pow = Одна оценка Argon2id на сессию: { $memory } МБ, { $rounds } прохода. Считает ваше устройство — это налог на масштаб, а не на вас.
ui-mine-mined = добыто
ui-mine-swings = ударов
ui-mine-timbers = крепей
ui-mine-swing = Бить
ui-mine-timber = Ставить крепь
ui-mine-leave = Уйти
ui-mine-pace = темп: { $fast ->
        [true] быстрый
       *[false] ровный
    }
ui-mine-collapsed = Обвал. Всё добытое за сессию потеряно.
ui-mine-collapsed-lost = добыто: { $lost }
ui-mine-rubble = Свод уже лёг: порода идёт в отвал, пока завал не разобран.
ui-mine-rubble-out = Завал разобран. Забой начинается заново.
ui-mine-last-cave-in = Следующий обвал для этого тела последний: оно останется под породой, а всё, что несёт, будет лежать здесь целым.

## Буровая: капитал вместо труда.

ui-rig-title = Буровая
ui-rig-rule = Машина не спит, но проигрывает человеку во всём остальном: выход ниже, качество ограничено настройкой, жилу выедает вдвое быстрее. Уголь возят люди, бункер вывозят люди, износ чинят люди — капитал нанимает общество, а не освобождает от него.
ui-rig-hopper = { $resource } · в бункере { $hopper } из { $capacity }
ui-rig-full = бункер полон, машина стоит
ui-rig-state = угля на { $hours } ч ({ $fuel }) · состояние { $condition } · в жиле { $left }
ui-rig-no-fuel = топливо кончилось, машина стоит
ui-rig-empty = Вывезти бункер
ui-rig-in-hands = Установка в руках. Поставьте её на жилу — дальше она работает без вас, пока есть уголь и место в бункере.
ui-rig-place = Поставить на жилу
ui-rig-down = Установка лежит, а не стоит: снятая или упавшая машина не бурит. Бункер вывезти можно и так, а чтобы она работала — поставьте её на жилу.

## Огород: делянки, симптомы и работа ногами.

ui-farm-land = Земля
ui-farm-title = Огород
ui-farm-owned = Участок { $owner }. Чужим хозяйством не управляют: наём — это доступ плюс доля через договор.
ui-farm-civic = Городская земля: чтобы вести здесь хозяйство, участок надо выкупить в окне «Участок».
ui-farm-unmarked = Земля не размечена. Сто метров — это столько делянок, сколько вы нарежете.
ui-farm-symptom-thirst = листья вялые
ui-farm-symptom-soaked = желтеют нижние листья
ui-farm-symptom-pale = бледный лист
ui-farm-symptom-burn = ожог по краю листа
ui-farm-symptom-fat = жирует в ботву
ui-farm-symptom-weedy = сорняк
ui-farm-symptom-crowded = тесно
# Тепло суток за полосой культуры (D-338): видно весь день.
ui-farm-symptom-chilled = лист прихвачен холодом
ui-farm-symptom-heat = лист скручивается от зноя
# Знаки напастей (D-299): что видно глазом. Чем гасят — в тексте агротехники.
ui-farm-symptom-spots = пятна на листе
ui-farm-symptom-web = паутина
ui-farm-symptom-bitten = погрызы
ui-farm-symptom-rot = гниль в пазухе
ui-farm-state-idle = под паром
ui-farm-state-plowing = пашется
ui-farm-state-plowed = вспахана
ui-farm-state-sown = растёт
ui-farm-moisture = влага
ui-farm-moisture-reading = влага { $value }
ui-farm-carried = воду носят руками
ui-farm-fed-stage = в эту фазу уже кормили
ui-farm-target = до какой влаги полить
ui-farm-water-to = Полить: { $target }
ui-farm-feed = Подкормить: { $goods }
ui-farm-weed = Прополоть
ui-farm-thin = Проредить
ui-farm-thin-why = один раз и только рано: выдернутое не вернуть
ui-farm-thinned = прорежена
ui-farm-guarded = защита: { $guard }
ui-farm-treat = Обработать: { $goods }
ui-farm-treat-why = держит свою напасть, пока держится средство; пришедшую только останавливает, но не лечит
ui-farm-stage-sprout = всходы
ui-farm-stage-leaf = лист
ui-farm-stage-bloom = цветение
ui-farm-stage-fill = налив
ui-farm-stage-ripe = спелость
ui-farm-health-strong = стоит крепко
ui-farm-health-weak = слабеет
ui-farm-health-sick = болеет
ui-farm-health-dying = гибнет
ui-farm-ripe = созрело — пора убирать
ui-farm-area = { $area } м²
ui-farm-fertility = плодородие
ui-farm-fertilize = Удобрить: { $goods }
ui-farm-plow = Вспахать
ui-farm-plow-pause = Приостановить
ui-farm-plow-pause-why = сделанное сохранится, продолжить можно отсюда
ui-farm-plow-resume = Продолжить вспашку
ui-farm-plow-reset = Сбросить вспашку
ui-farm-plow-reset-why = делянка снова под паром, сделанное пропадёт
ui-farm-plow-paused = приостановлена · вспахано { $share }%
ui-farm-plow-share = вспахано { $share }%
ui-farm-no-seeds = — семян нет —
ui-farm-vigor = сила { $vigor }
ui-farm-sow = Посеять
ui-farm-harvest-select = Убрать с отбором
ui-farm-harvest-select-hint = отобрать лучшие растения на семена: фонд держит силу
ui-farm-harvest = Убрать
ui-farm-harvest-hint = убрать не глядя: семенной фонд потеряет силу
ui-farm-new-plot = Новую делянку размечают в окне «Земля»: межевание — дело земли, а не земледелия.
ui-farm-rule = Делянка живёт тремя шкалами — влага, здоровье, рост, — и кривой видна только влага: она уходит долей от текущей, у реки вдвое медленнее. Полив до цели и подкормка в фазу — отдельные действия ногами; засуха и перелив губят, не то удобрение жжёт. Как ухаживать — текст в Библиотеке. Монокультура истощает землю, чередование и пар лечат — межа помнит, что на ней росло.
ui-farm-seeds-rule = Сеют семенами: у партии свой сорт и своя сила, и урожай считается по ним. Часть урожая остаётся своим семенем — с отбором фонд держится, без отбора вырождается, а гибрид ещё и расщепляется.

## Топливная станция: запас, расход и засыпка.

ui-plant-fuel = топлива
ui-plant-lasts = хватит на
ui-plant-burn = жжёт { $draw } { $fuel } в час и даёт { $output } энергии
ui-plant-count = станций { $count }
ui-plant-at-hand = в руках { $amount }
ui-plant-pour = Засыпать { $fuel }
ui-plant-given = Засыпанное — городу: обратно топливо не берут.
ui-plant-none = { $fuel } в руках нет. Станция стоит на подвозе: без топлива город сидит без энергии.

## Селекционный питомник: скрещивание и сорта.

ui-nursery-title = Селекционный питомник
ui-nursery-first = — первый родитель —
ui-nursery-second = — второй родитель —
ui-nursery-variety = сорт
ui-nursery-cross = Скрестить
ui-nursery-rule = Скрещивают сорта одной культуры. Одна попытка стоит семян, места и полного цикла роста: селекция — занятие на недели, а не на вечер.
ui-nursery-beds = В питомнике
ui-nursery-sprouts = всходы { $when }
ui-nursery-gather = Забрать всходы
ui-nursery-sprouted = взошло: новый гибрид у вас в руках
ui-nursery-failed = не взошло: вышедшее слишком похоже на уже растущее
ui-nursery-own = Свои сорта
ui-nursery-own-rule = Гибрид даёт отличный урожай один раз — его семена расщепляются. Поколения отбора доводят его до постоянного сорта, и тогда автор даёт ему имя навсегда.
ui-nursery-hybrid = гибрид, поколение { $generation }
ui-nursery-row = { $stable ->
        [true] постоянный
       *[false] расщепляется
    } · урожай { $yield } · цикл { $cycle } сут
ui-nursery-name = имя сорта
ui-nursery-name-set = Назвать

## Собирательство: пустая земля отдаёт то, что на ней лежит.

ui-forage-title = Собирательство
ui-forage-rule = Пустая земля — участок без пятна застройки — отдаёт то, что на ней лежит. Что найдётся, не выбирают: поиск идёт временем, по сроку земля показывает одну находку. Нужна — подобрать в руки, и на этом поиск закончен: идти по участку снова или уйти, решаете вы. Не нужна — «искать дальше», и поиск продолжится сам. Каждый поиск стоит сил — найден он или пропущен. Чем больше пустой земли, тем быстрее находка. Уйдёте с места — поиск прервётся вместе с ненайденным.
ui-forage-area = пустой земли { $area } м²
ui-forage-about = находка примерно за { $term }
ui-forage-cost = сил { $stamina } за поиск
ui-forage-took = подобрано:
ui-forage-done = поиск закончен: искать дальше или уйти
ui-forage-found = нашлось:
ui-forage-find = { $mass } кг · кач. { $quality }
ui-forage-start = Начать собирательство
ui-forage-start-hint = пойти по участку: находка покажется по сроку
ui-forage-barred = здесь больше не ищут: земля чужая или застроена
ui-forage-again = Искать дальше
ui-forage-pass-hint = оставить лежать — и искать дальше
ui-forage-take = Подобрать
ui-forage-take-hint = в руки; поиск на этом заканчивается — искать дальше решать вам
ui-forage-stop = Закончить
ui-forage-stop-hint = закончить: потраченные силы не вернутся
ui-forage-stop-hint-took = закончить: находка уже в руках
ui-forage-stop-hint-found = закончить собирательство; находка останется лежать
ui-forage-searching = ищете · находка покажется через
ui-forage-label = поиск
ui-forage-finds = здесь находят:

## Цех: нод-едитор автоматов (D-253, волна 5).

ui-factory-title = Цех
ui-factory-rule = машины и провода
ui-factory-hint = Провод ведут от правой точки машины к левой точке другой: что кормит — левее того, что ест. Провод режется щелчком по нему.
ui-factory-wire-armed = Выход взят — щёлкните левую точку машины, которую он кормит. Щелчок по выходу ещё раз — отмена.
ui-factory-unlink = перерезать провод
ui-factory-port-in = вход
ui-factory-port-out = выход
ui-factory-idle = — без программы —
ui-factory-backlog = в работе { $backlog }
# Почему автомат стоит (D-340): на земле сбросному газу некуда без факела,
# на борту причину показывает схема корабля.
ui-factory-stall-flare = стоит: в узле нет факельной установки для сбросного газа
ui-factory-stall-lines = стоит: причина — на схеме корабля

## Ползунок курса: от самой быстрой дуги до самой дешёвой (D-271).

ui-ship-course-loading = Небо считает дуги…
ui-ship-no-arc-fits = Ни одну дугу двигатели не вытянут: снимите массу или ставьте двигатели.
ui-ship-slider = время полёта
ui-ship-end-fast = быстро: { $term }
ui-ship-end-cheap = дёшево: { $term }
ui-ship-arc-cost = { $term } · { $fuel } топлива · Δv { $dv }
ui-ship-chart-cheap = дёшево { $term } · { $fuel }
ui-ship-chart-fast = быстро { $term } · { $fuel }
# The bridge display's own words (D-240): the scale in the corner and the names
# of the three lines ahead. Short on purpose -- they stand inside the drawing.
ui-ship-chart-scale = ×{ NUMBER($zoom, minimumFractionDigits: 1, maximumFractionDigits: 1) }
ui-ship-chart-inertia = инерция
ui-ship-chart-course = курс
ui-ship-chart-choice = выбор
# The ring round the hull and the slider beside it. «Видимость» is the world's
# own word for the radius: `ship-target-unseen` says «чужой корпус виден ближе».
ui-ship-chart-sight = видимость
ui-ship-chart-zoom = приближение
# The sky flown, not tabled (D-289): the drift, its verdict, and the Δv the
# console reads the plan against.
ui-ship-fate-stable = Инерция: устойчивый круг. Так можно висеть вечно; заправят — можно прокладывать курс.
ui-ship-fate-crash = Инерция: столкновение · { $body }. Не заправят вовремя — корабль погибнет.
ui-ship-fate-escape = Инерция: прочь из системы. Не заправят вовремя — корабль погибнет.
ui-ship-fate-label = дрейф
ui-ship-lost-status = погиб
ui-ship-lost-note = Корабль погиб вместе с экипажем: приказов ему больше не отдать.
# Two hulls meeting (D-289, wave 3): the rendezvous, the hold, the consent to dock.
ui-ship-course-to-ship = встреча · { $name }
ui-ship-target-gone = Цель вышла из виду.
ui-ship-held = на удержании · { $name }
ui-ship-docked-ship = пристыкован · { $name }
ui-ship-dock = Состыковаться
ui-ship-dock-agree = Согласиться на стыковку
ui-ship-dock-asked = согласие дано · второй командир ещё не ответил
ui-ship-dock-wanted = второй командир просит стыковки
ui-ship-dock-hint = Стыковка борт к борту — с согласия обоих командиров; трап откроет экипажу дорогу с канистрами.
ui-ship-undock = Отстыковаться
ui-ship-star = звезда
ui-ship-dv-line = на борту Δv { $have }
ui-ship-short-cross = В баках { $fuel }, а на переход нужно { $need }: топливо кончится в пути, и корабль ляжет в дрейф.
ui-ship-short-land = В баках { $fuel }, а с посадкой в конце нужно { $need }: корабль дойдёт до орбиты и на ней останется.
ui-ship-course-dv = Δv до цели { $need } · на борту { $have }
ui-ship-course-short = Δv на борту меньше, чем нужно на переход: баки опустеют в пути, и корабль ляжет в дрейф.
ui-ship-course-failed = Небо не ответило: { $why }

## Разведка: точка на земле и отправка тела (D-321).

ui-map-scout = разведка
ui-map-join = путь
ui-map-time = время
ui-map-join-aim = Путь к узлу «{ $node }»: { $metres } м
ui-map-join-go = Проложить путь
ui-map-survey-aim = Точка разведки: { $metres } м от вас
ui-map-survey = Разведать
ui-map-survey-clear = Снять точку
ui-map-peek-asking = читаю поле…
ui-map-peek-found = уже найдено
ui-map-peek-found-there = путь ляжет туда
ui-map-peek-ground = земля
ui-map-peek-water = вода
ui-map-peek-water-river = река
ui-map-peek-water-lake = озеро
ui-map-peek-water-none = нет
ui-map-peek-stream = ручей { $percent }%
ui-map-peek-mountain = горы
ui-map-peek-climate = климат
ui-map-peek-climate-value = { $c }° ± { $swing }°, осадки { $rain } из 100
ui-map-peek-moisture = влажность почвы
ui-map-peek-marks = приметы, шансы
ui-map-peek-mark = { $mark } { $percent }%
ui-map-peek-vein = шанс жилы
ui-map-peek-complex = шанс комплекса
ui-map-peek-percent = { $percent }%
ui-map-probe-height = { $m } м
ui-map-probe-rain = осадки { $percent } из 100
ui-map-probe-moisture = влажность { $percent }%
ui-map-probe-weather-rain = дождь { $percent }%
ui-map-probe-weather-cloudy = облачно
ui-map-probe-weather-clear = ясно
