# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова окон места: участок, здание, этаж, отсек, пол, хранилища, обоз,
# добыча — и ряд объектов узла, которым они открываются (D-251, волна IV).
#
# Здесь же владения и бумаги: они про то же имущество, только счёт за него
# приходит в боковую вкладку, а не на месте.
#
# Значение — одной строкой (перенос попал бы в текст); варианты выбора —
# каждый на своей строке, эти переносы в текст не попадают.

## Общее для окон места: одна подпись — одна строка, где бы её ни показывали.

ui-place-cancel = Отмена
ui-place-empty = пусто
ui-place-area = { $area } м²
ui-place-slots = мест под оборудование { $used } из { $slots }
ui-place-materials-have = { $have } из { $need }
ui-place-short = Не хватает: { $what }

## Знаки земли: заголовок окна добычи. Ключи — свойства узла, слова — наши.

ui-place-sign-woods = Лес
ui-place-sign-stones = Камни
ui-place-sign-meadow = Луг

## Участок: чей он, как зовётся, кого пускает и во что обходится.

ui-place-plot-title = Участок
ui-place-plot-mine = ваш участок
ui-place-plot-owner = хозяин { $owner }
ui-place-plot-city = земля города { $city }
ui-place-plot-nobody = ничей
ui-place-plot-gated = { " " }· закрыта для входа
ui-place-plot-cut-off = { " " }· отключена за неуплату
ui-place-plot-tax = Земельный налог: { $tax } ₭ в сутки со всей площади участка — застроен он или нет. Чем дальше от биопринтера, тем ставка ниже.
ui-place-plot-upkeep-owner = За электричество здесь платите вы: счёт идёт с площади раз в период.
ui-place-plot-upkeep-city = Узел содержит город{ $named ->
        [true] { " " }{ $city }
       *[false] {""}
    }: энергия уходит из городского пула, деньгами счёт не выставляется.
ui-place-plot-upkeep-nobody = Счётчика здесь нет: у узла нет хозяина, и выставлять счёт некому.
ui-place-plot-upkeep-none = Городской сети здесь нет: счёта за электричество не бывает, работают от аккумулятора.
ui-place-plot-cede = Передать городу
ui-place-plot-cede-note = Счётчик перейдёт на казну: городской узел жжёт энергию из пула, и деньгами за него никто не платит. Оборудование останется на месте, но распоряжаться им будет власть, а не вы.
ui-place-plot-cede-yes = Да, передать городу
ui-place-plot-cede-rule = Бумага на землю погашается, участок станет городским. Вернуть его можно только выкупом по прейскуранту — как любой другой.
ui-place-plot-wild = Земля за городом ничья и таковой остаётся: бумагу на владение выдаёт город, а здесь его нет. Работать и строить тут может всякий — поставленное принадлежит поставившему.
ui-place-plot-buy = Выкупить за { $price } ₭
ui-place-plot-buy-note = Цена от удалённости до биопринтера: деньги в казну, вам — бумага на землю.
ui-place-plot-name-hint = как называть это место
ui-place-plot-rename = Переименовать
ui-place-plot-rename-note = Имя увидят все на карте; ключ локации не меняется.
ui-place-plot-emblem-label = значок узла на карте
ui-place-plot-emblem-nail = Прибить значок
ui-place-plot-emblem-clear = Снять
ui-place-plot-emblem-clear-hint = узел вернётся к значку своей земли
ui-place-plot-about-label = описание места
ui-place-plot-about-hint = что это за место — увидит всякий вошедший
ui-place-plot-about-save = Сохранить описание

## Делянки: земля родит их, а работа с ними — в огороде.

ui-place-marking-title = Делянки
ui-place-marking-climate = Здесь { $climate }: в открытом грунте ничего не растёт, и обогрев узла этого не меняет. Еда сюда приходит кораблём.
ui-place-marking-name = имя делянки
ui-place-marking-area = площадь, м²
ui-place-marking-mark = Разметить
ui-place-marking-marked = Размечено делянок: { $count }. Работа с ними — в окне «Огород».
ui-place-marking-none = Размеченная делянка откроет окно «Огород»: вспашка, посев, уход и уборка.

## Вход: закрыть, открыть и два списка.

ui-place-door-title = Вход
ui-place-door-rule = Вошедший распоряжается тем, что лежит на земле: дверь и сундук — защита, а не правило «не бери».
ui-place-door-strike = убрать из списка
ui-place-door-open = Открыть вход
ui-place-door-shut = Закрыть вход
ui-place-door-is-shut = Закрыта: входят хозяин и белый список.
ui-place-door-is-open = Открыта: входят все, кроме чёрного списка.
ui-place-door-through = { " " }Пройти насквозь можно всегда — и выйти тоже: закрыть вход при госте нельзя.
ui-place-door-who = имя
ui-place-door-allow-hint = кого пускать в закрытую локацию
ui-place-door-allow = В белый список
ui-place-door-allowed = Пускаем:
ui-place-door-allowed-shut = Пока никого: входите только вы.
ui-place-door-allowed-open = Пригодится, когда закроете вход.
ui-place-door-bar-hint = кого не пускать вовсе
ui-place-door-bar = В чёрный список
ui-place-door-barred = Не пускаем:
ui-place-door-barred-none = Чёрный список сильнее белого: названный тут не войдёт.

## Основание города: порог входа — постройки, а не монета.

ui-place-foundation-title = Основание города
ui-place-foundation-name = название города
ui-place-foundation-found = Основать город
ui-place-foundation-ready = Земля отойдёт городу, основатель получит все полномочия.
ui-place-foundation-threshold = Порог входа — постройки, а не монета.

## Здание: стройка, ремонт, снос.

ui-place-house-title = Здание
ui-place-house-default = Дом
ui-place-house-summary = { $area } м² в { $floors } эт. на { $ground } м² земли · мест на первом этаже { $used } из { $slots }
ui-place-house-condition = состояние
ui-place-house-decay = порча ·
ui-place-house-decay-rate = −{ $decay }%/сут
ui-place-house-decay-hint = порча идёт каждые сутки; чинят в этом же окне
ui-place-house-storeys = Этажей выше первого: { $count }. На каждый ведёт лестница, и у каждого свой пол и свои места под оборудование — они на карте рядом.
ui-place-house-none = Дома нет — только двор. Рабочие станции и мебель ставят в дом: сначала строят.
ui-place-house-site = стройка: { $area } м² в { $floors } эт.
ui-place-house-site-kind = { " " }({ $kind })
ui-place-house-site-note = материалы уже в стене
ui-place-house-site-label = стройка
ui-place-house-kind-hint = тип здания
ui-place-house-kind-option = { $kind } · этаж ×{ $growth } · порча { $decay }%/сут
ui-place-house-footprint = пятно застройки, м²
ui-place-house-floors = этажей
ui-place-house-plan = { $area } м² × { $floors } эт. = { $living } м² жилой площади, и каждый этаж выше первого станет отдельным местом с лестницей. Свободно { $free } м² двора, меньше { $least } м² не строится. Этажность не ограничена — за высоту платит смета.
ui-place-house-counting = Смета считается сама, пока вы выбираете.
ui-place-house-build = Строить { $area } м² в { $floors } эт.
ui-place-house-term = Работы на { $hours } ч; { $kind }.

## Ремонт дома: чинят тем же, чем построено.

ui-place-repair-estimate = Посчитать ремонт
ui-place-repair-whole = Дом целёхонек: чинить в нём нечего.
ui-place-repair-condition = Состояние { $condition }%. На нуле дом обрушится вместе с тем, что стоит во дворе.
ui-place-repair-do = Чинить
ui-place-repair-going = Ремонт уже идёт.
ui-place-repair-term = Работы на { $hours } ч; чинят тем же, чем построено.

## Снос: двор пустеет до сноса, а не после.

ui-place-demolition-estimate = Посчитать снос
ui-place-demolition-going = Пока идёт стройка, сносить нечего: дождитесь её конца.
ui-place-demolition-rule = Снос — работа: часть материалов вернётся, остальное сломается при разборе.
ui-place-demolition-back = вернётся { $amount }
ui-place-demolition-do = Снести { $area } м²
ui-place-demolition-hint = работы идут временем, материалы придут в конце
ui-place-demolition-blocked-hint = двор пустеет до сноса, а не после
ui-place-demolition-blocking = Сначала: { $what }
ui-place-demolition-term = Работы на { $hours } ч. Участок станет пустым.

## Станки и мебель: что стоит в здании и что можно поставить.

ui-place-equipment-stations = Рабочие станции
ui-place-equipment-stations-rule = За рабочей станцией работает один: пока идёт партия, второму она не отдаётся.
ui-place-equipment-furniture = Мебель
ui-place-equipment-furniture-rule = Мебель обустраивает быт: кровать — сон быстрее, сундук — хранение. На ней не работают.
ui-place-equipment-quality = { " " }· качество { $quality }
ui-place-equipment-condition = { " " }· сост. { $condition }
ui-place-equipment-charge = заряд { $charge } · заряжают в «хозяйстве»
ui-place-equipment-busy-mine = занята вами
ui-place-equipment-busy = занята
ui-place-equipment-free = свободна
ui-place-equipment-take = Забрать
ui-place-equipment-take-hint = забрать в руки
ui-place-equipment-drop-station = перетащите сюда станок, чтобы поставить его в здание
ui-place-equipment-drop-furniture = перетащите сюда мебель, чтобы обставить здание
ui-place-equipment-place = Поставить:
ui-place-equipment-place-hint = поставить в здание
ui-place-equipment-no-room = в здании нет свободных мест
ui-place-equipment-no-room-hint = в здании нет места: стройте больше либо уносите лишнее
ui-place-equipment-slots = { " " }Каждая занимает { $area } м² здания: мест { $used } из { $slots }.

## Пол и земля: что тут лежит, и куда это девать.

ui-place-floor-title = На полу
ui-place-ground-title = На земле
ui-place-floor-taken = занято { $used } из { $area } м²
ui-place-floor-cargo = { " " }· груза { $mass } кг
ui-place-floor-gear = { " " }· оборудования { $count }
ui-place-floor-drop = перетащите сюда предмет, чтобы положить на пол
ui-place-ground-drop = перетащите сюда предмет, чтобы положить на землю
ui-place-floor-mass = · { $mass } кг
ui-place-floor-pick = Взять
ui-place-floor-pick-hint = взять в руки — сколько унесёте; строку можно и перетащить вниз
ui-place-floor-passing = Вы здесь проходом: чужая закрытая локация пола вам не отдаёт.
ui-place-floor-rule = Лежащее занимает площадь; в сундуке — не занимает. Обрушение дома хоронит то, что лежит под крышей.
ui-place-ground-rule = Лежащее занимает площадь двора — того, что осталось от участка вокруг дома. Дом упадёт — это уцелеет.
ui-place-floor-guest = Чужое место, но лежащее берёт всякий, кого сюда пустили.

## Хранилища: сундук и бак. Жидкость живёт только в таре.

ui-place-chest-rule = Дом хранит то, что не увезти в руках; полный сундук не уносят.
ui-place-chest-taken = занято { $mass } из { $capacity } кг
ui-place-chest-foreign = Чужое хранилище: что внутри — не ваше дело.
ui-place-chest-drop = перетащите сюда предмет, чтобы убрать в хранилище
ui-place-chest-take = Забрать
ui-place-chest-take-hint = забрать в руки — сколько унесёте; строку можно и перетащить вниз
ui-place-tank-rule = Тара берёт только жидкость, и жидкость живёт только в таре: в бак переливают из канистры и из бака — в канистру.
ui-place-tank-filled = налито { $mass } из { $capacity } кг
ui-place-tank-foreign = Чужой бак: что внутри — не ваше дело.
ui-place-tank-pour-out = В
ui-place-tank-pour-out-hint = слить в канистру — сколько войдёт и сколько унесёте
ui-place-tank-pour-in = Перелить из «{ $goods }»
ui-place-tank-need-canister = Нужна канистра в руках: жидкость не носят в ладонях.

## Этаж: своя комната наверху, а дом — внизу.

ui-place-storey-title = Этаж
ui-place-storey-which = { " " }· { $floor }-й из { $floors }
ui-place-storey-name = имя этажа
ui-place-storey-rename = Переименовать этаж
ui-place-storey-rule = Дом стоит на участке внизу: тип, состояние, ремонт и снос — там же. Обрушится он — этаж падёт с ним, и всё, что на нём стояло и лежало.

## Отсек: комната на борту. Земли под ней нет.

ui-place-berth-title = Отсек
ui-place-berth-name = имя отсека
ui-place-berth-rename = Переименовать отсек

## Имя месту: одна кнопка на этаж и на отсек.

ui-place-rename-save = Назвать

## Обоз: груз едет в трюме, а не в руках.

ui-place-convoy-title = Обоз
ui-place-convoy-harnessed = впряжён:
ui-place-convoy-hold = трюм
ui-place-convoy-hold-amount = { $mass } из { $capacity } кг
ui-place-convoy-speed = · скорость ×{ $speed } · сост. { $condition }
ui-place-convoy-drop = перетащите сюда предмет, чтобы погрузить в трюм
ui-place-convoy-unload = Выгрузить
ui-place-convoy-unload-hint = выгрузить в руки — сколько поместится; строку можно и перетащить вниз
ui-place-convoy-empty = трюм пуст
ui-place-convoy-unharness = Распрячься
ui-place-convoy-unharness-rule = Обоз останется здесь с грузом; по бездорожью он не идёт.
ui-place-convoy-harness = Впрячься:
ui-place-convoy-cart = { $capacity } кг · скорость ×{ $speed }
ui-place-convoy-no-capacity = вольт не назвал грузоподъёмности
ui-place-convoy-rule = Груз едет в трюме, а не в руках.

## Добыча по знаку земли: рубка, ломка, покос.

ui-place-gather-qty = сколько добыть
ui-place-gather-needs = нужен { $needs }; готовое — в «делах»
ui-place-gather-barehanded = голыми руками, потому и дольше; готовое — в «делах»
ui-place-gather-missing = нужен: { $needs }
ui-place-gather-rule = Партия идёт временем, готовое забирается в «делах». Валежник и прочее лежащее — в «Собирательстве».

## Реактор Предтеч: греет город и в свой срок гаснет.

ui-place-reactor-title = Наследие Предтеч
ui-place-reactor-rule = Реактор греет город и кормит маяк космодрома без топлива и без людей — но выход падает и в свой срок доходит до нуля. Дальше город держат те, кто в нём живёт: своя генерация, своя ТЭЦ. Погаснет последний работающий космодром планеты — сесть будет некуда, и планета потеряна.
ui-place-reactor-when = гаснет
ui-place-reactor-out = погас
ui-place-reactor-days = { $days } сут
ui-place-reactor-already = уже погас
ui-place-reactor-in = через { $days } сут
ui-place-reactor-warning = Реактор на исходе: без своей генерации город остынет, а космодром погаснет вместе с ним.

## Ряд объектов узла: имя плитки, что она делает и зачем окно.

ui-stand-nothing = Здесь ничего не стоит — только дороги.
ui-stand-busy-mine = занята вами
ui-stand-busy = занята
ui-stand-free = свободна
ui-stand-quality = кач. { $quality }
ui-stand-condition = сост. { $condition }
ui-stand-mine = Забой
ui-stand-mine-going = сессия идёт
ui-stand-mine-vein = жила: { $goods }
ui-stand-mine-about = Окно забоя: спуститься в жилу и рубить, порода за породой.
ui-stand-batch = партия · { $goods }
ui-stand-bench-about = Окно рабочей станции «{ $machine }»: партии по рецептам, ремонт и попытки без рецепта.
ui-stand-trade-kitchen = { " " }Здесь готовят еду.
ui-stand-trade-nursery = { " " }Здесь разводят животных.
ui-stand-trade-fuel-plant = { " " }Здесь гонят корабельное топливо.
ui-stand-trade-mint = { " " }Здесь чеканят монету города.
ui-stand-gather-about = Добыча по знаку земли: работа руками прямо на месте.
ui-stand-rig = Буровая
ui-stand-rig-in-hands = в руках: поставить на жилу
ui-stand-rig-about = Окно буровой: поставить на жилу и бурить вглубь.
ui-stand-console-about = Окно рубки: карта рейса этого корабля, подъём на орбиту, курс и посадка.
ui-stand-console-aground = работает только на борту корабля
ui-stand-ground-console-about = Окно наземной консоли: свои корабли где бы они ни были — карта рейса, подъём, курс, посадка и разворот.
ui-stand-ship = Корабль
ui-stand-ship-about = Окно корабля: тяга против массы, кислород, имя и чертёж — расстановка отсеков.
ui-stand-yard-about = Окно верфи: заложить корпус и смотреть швартовку.
ui-stand-farm = Огород
ui-stand-farm-strips = { $count ->
        [1] одна делянка
       *[other] делянок: { $count }
    }
ui-stand-farm-about = Окно огорода: вспашка, посев, ежедневный уход и уборка делянок.
ui-stand-forage = Собирательство
ui-stand-forage-found = нашлось: { $goods } ×{ $units }
ui-stand-forage-searching = идёт поиск
ui-stand-forage-area = { $area } м² пустой земли
ui-stand-forage-about = Окно собирательства: поиск полезного на пустой земле.
ui-stand-library = Библиотека
ui-stand-library-about = Окно библиотеки: взять рецепты и отдать свои.
ui-stand-hall = Администрация
ui-stand-hall-about = Окно администрации: гражданство, власть, суд и законы города.
ui-stand-market-mine = вашего товара: { $count }
ui-stand-market-about = Окно рынка: стакан заявок, покупка, продажа и свой товар в терминале.
ui-stand-convoy = Обоз
ui-stand-convoy-hold = трюм { $mass } из { $capacity } кг
ui-stand-convoy-standing = стоит: { $goods }
ui-stand-convoy-about = Окно обоза: впрячься и возить в трюме больше, чем унесут руки.
ui-stand-floor = пол { $used } / { $area } м²
ui-stand-berth = Отсек
ui-stand-berth-about = Окно отсека: станки и мебель на борту, пол отсека с вещами и имя отсека.
ui-stand-storey = Этаж
ui-stand-storey-about = Окно этажа: станки и мебель на этом этаже, его пол с вещами и имя этажа.
ui-stand-house = Здание
ui-stand-house-size = { $area } м² в { $floors } эт.
ui-stand-house-floor = { " " }· пол { $used } / { $area } м²
ui-stand-house-condition = { " " }· состояние { $condition }%
ui-stand-house-building = строится
ui-stand-house-absent = не построен
ui-stand-house-about = Окно здания: стройка, ремонт, снос и расстановка станков и мебели — и то, что лежит на полу здания и в его хранилищах.
ui-stand-reactor = Реактор Предтеч
ui-stand-reactor-about = Окно реактора Предтеч: сколько энергии осталось городу.
ui-stand-plot = Земля
ui-stand-plot-cut-off = отключена за неуплату
ui-stand-plot-gated = вход закрыт
ui-stand-plot-price = продаётся за { $price } ₭
ui-stand-plot-wild = ничья земля
ui-stand-plot-ground = лежит { $used } / { $area } м²
ui-stand-plot-owner = хозяин { $owner }
ui-stand-plot-city = город { $city }
ui-stand-plot-about = Окно земли: управление локацией — имя, значок и описание узла, доступ, выкуп и основание города — и то, что лежит на земле.
ui-stand-plot-bare-about = Окно земли: то, что лежит здесь на земле — положить и взять.

## Владения: сеть, аккумуляторы, счета и бумаги на землю.

ui-holdings-stale = Сервер не ответил: то, что ниже, — прошлое чтение. Нажмите «обновить».
ui-holdings-grid = Городская сеть
ui-holdings-grid-rule = Городские постройки содержит казна: энергия, которую они жгут, — расход города, а не посетителя.
ui-holdings-grid-asking = Сеть опрашивается…
ui-holdings-grid-pool = { $city }: в пуле { $stored } · тариф { $tariff } ₭ за 100
ui-holdings-grid-none = Здесь городской сети нет: вне города работают от аккумулятора, и заряжают его в городе.
ui-holdings-batteries = Аккумуляторы
ui-holdings-batteries-none = Аккумулятора нет: энергия либо в пуле города, либо в аккумуляторе.
ui-holdings-in-hands = в руках
ui-holdings-here = стоит здесь
ui-holdings-charge = Зарядить
ui-holdings-charge-hint = залить доверху по тарифу
ui-holdings-charge-asking = сеть ещё опрашивается
ui-holdings-charge-no-grid = здесь нет сети
ui-holdings-title = Владения и счета
ui-holdings-area = { $area } м²
ui-holdings-cut-off = · отключён
ui-holdings-per-period = { $cost } ₭ / период
ui-holdings-no-grid = нет сети
ui-holdings-debt = долг { $amount } ₭
ui-holdings-pay = Оплатить
ui-holdings-bill-rule = Счёт считается с площади — свет, тепло, вентиляция. Не заплатил — узел отключён, и рабочие станции в нём стоят, пока долг не закрыт. Отобрать узел за долг движок не вправе: это решение суда.
ui-holdings-debt-total = { " " }Сейчас долгов на { $amount } ₭.
ui-holdings-deeds = Ценные бумаги
ui-holdings-deeds-rule = Бумага — электронный документ: живёт в Сети, переживает тело и продаётся отсюда, хоть с дороги. Титул на участок переходит вместе с ней.
ui-holdings-deeds-none = Своих бумаг нет. Бумага появляется с участком: выкупили или заняли землю — владение оформлено документом.
ui-holdings-deed-area = · { $area } м²
ui-holdings-deed-sale = продаётся за { $price } ₭
ui-holdings-deed-sale-to = { " " }· для { $who }
ui-holdings-deed-not-sold = не продаётся
ui-holdings-price = цена, ₭
ui-holdings-price-hint = цена договора, ₭
ui-holdings-to-whom = кому (пусто — всем)
ui-holdings-sell = Продать
ui-holdings-unsell = Снять с продажи
ui-holdings-market = Бумаги на продажу
ui-holdings-deed-market-area = · { $area } м² · у { $owner }
ui-holdings-buy = Купить
