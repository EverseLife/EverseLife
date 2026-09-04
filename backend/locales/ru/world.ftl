# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Мир под ногами: земля и еда, вода и воздух, холод и тепло, руда и развалины,
# сон, износ и смерть (D-251, волна III).
#
# NAME($id) превращает устойчивый ключ вещи, станции или класса в слово этого
# языка: по проводу едет `iron_ore`, читатель видит «Железная руда». До этой
# волны несколько отказов печатали ключ прямо в русскую фразу — здесь их нет.
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.

# --- земля и делянки (engine/farm.py) ----------------------------------------

farm-too-small = меньше { $min } м² межевать бессмысленно
farm-body-off-node = тело вне узла
farm-storey-not-ground = это этаж, а не земля: делянку режут во дворе — спуститесь вниз
farm-node-not-yours = участок не ваш: городскую землю выкупают, а чужую — арендуют по договору
farm-no-land = в узле { $node } свободно { $free } м², просят { $area }
# Состояние делянки — значение перечисления (`PlotState`), словом становится
# здесь: до этого игрок читал «не под паром: plowing». `idle` сюда не доходит
# — отказ и поднимается ровно тогда, когда делянка не под паром, — но своя
# ветка у него есть: тест требует её от каждого члена перечисления, а ловушка
# по умолчанию оставлена невозможному значению, и только ему.
farm-not-fallow = делянка «{ $plot }» не под паром: { $state ->
        [plowing] идёт вспашка
        [plowed] уже вспахана
        [sown] засеяна
        [idle] под паром
       *[other] { $state }
    }
farm-not-plowing = плуг стоит: ни одна делянка сейчас не под вспашкой
farm-plot-not-plowing = делянка «{ $plot }» не под вспашкой
farm-plow-running = вспашка «{ $plot }» ещё идёт: сначала приостановите её
farm-job-no-plot = задание { $job }: делянки нет
farm-not-plowed = делянка «{ $plot }» не вспахана
farm-wrong-seeds = «{ NAME($goods) }» — не семена культуры «{ CULTURE($culture) }»
farm-seeds-not-in-hands = семена не в руках: сеют своим
farm-not-enough-seeds = нужно { $need } «{ NAME($seeds) }» на посев, есть { $have }
farm-nothing-grows = на делянке «{ $plot }» ничего не растёт
farm-already-wetter = «{ $plot }» и так влажнее: влага { $moisture }, цель { $target }
farm-feed-ripe = делянка «{ $plot }» созрела: кормить больше нечего — убирайте
farm-thinned-already = делянка «{ $plot }» уже прорежена: второй раз дёргать нечего
farm-thin-late = делянка «{ $plot }» переросла прореживание: прореживают { $until ->
        [sprout] на всходах
        [leaf] на всходах или в лист
        [bloom] на всходах, в лист или в цветение
       *[fill] на всходах, в лист, в цветение или в налив
    }
farm-fertilize-sown = { $state ->
        [plowing] «{ $plot }» под плугом: удобряют пар или вспаханное, а вспашку сначала заканчивают или сбрасывают
       *[other] делянка «{ $plot }» засеяна: удобряют землю, а не растущее — растущее подкармливают («Подкормить»)
    }
farm-not-a-fertilizer = «{ NAME($goods) }» — не удобрение: землю кормят компостом или минеральным
farm-no-fertilizer = нужно { $need } «{ NAME($goods) }»: норма внесения считается по площади
farm-not-a-protectant = «{ NAME($goods) }» — не средство защиты: делянку обрабатывают фунгицидом, акарицидом, инсектицидом или бактерицидом
farm-no-protectant = нужно { $need } «{ NAME($goods) }»: норма обработки считается по площади
farm-land-sated = «{ $plot }» и так сыта: плодородие на потолке, удобрение ушло бы впустую
farm-no-water = нужно { $need } воды: реки здесь нет, воду носят руками
farm-too-cold = «{ $culture }» здесь вымерзнет: ночь опускается до { $night }°
farm-too-hot = «{ $culture }» здесь сгорит: полдень доходит до { $noon }°
farm-too-dark = «{ $culture }» просит света { $need }, а это место даёт { $light }: лес и стены застят небо
farm-nothing-to-harvest = на делянке «{ $plot }» нечего убирать
farm-not-ripe = делянка «{ $plot }» ещё не созрела: { $stage ->
        [sprout] всходы
        [leaf] лист
        [bloom] цветение
        [fill] налив
       *[other] растёт
    }
farm-halves-too-small = обе части обязаны быть не меньше farm.plot_min_area
farm-merge-other-node = сливают соседние делянки, а не землю из разных узлов
farm-no-open-ground = «{ $node }»: { NAME($weather) } — в открытом грунте здесь ничего не растёт. Еда сюда приходит кораблём
farm-dead-works = мёртвое тело не работает
farm-plot-not-yours = чужая делянка: аренда и наём — через договор
farm-recut-sown = { $state ->
        [plowing] делянка под плугом: сначала закончите или сбросьте вспашку
       *[other] перекроить можно только незасеянное
    }

# --- еда (engine/food.py) ----------------------------------------------------

food-dead-eats = мёртвые не едят
food-asleep = тело спит: сначала проснуться
food-not-in-hands = еда не в руках: едят своё и из рук
food-not-food = «{ NAME($goods) }» не еда
food-spoiled = «{ NAME($goods) }» испортилось

# --- селекция (engine/breed.py) ----------------------------------------------

breed-no-drift-in-formula = из формулы «{ $formula }» не вычитать коэффициент отклонения
breed-dead-sows = мёртвое тело не сеет
breed-no-nursery = в узле нет постройки класса «{ NAME($station) }»
# CULTURE() — свой домен: `beans` среди товаров это зерно, а не культура.
breed-different-cultures = { CULTURE($one) } и { CULTURE($other) } — разные культуры: скрещивают сорта одной
breed-one-batch = нужны две партии семян: сорт сам с собой не скрещивают
breed-not-enough-seeds = на питомник нужно { $need } семян каждого сорта
breed-nursery-done = этот питомник уже разобран
breed-nursery-not-ready = питомник созреет: { $left }
thing-gone = { NAME($goods) } здесь больше нет: пока вы тянулись, это унесли
breed-parent-gone = родительский сорт исчез
breed-not-stable = сорт ещё не постоянен: имя даётся тому, что даёт тот же результат из раза в раз
breed-not-the-author = называет сорт тот, кто его вывел
breed-empty-name = имя пустое
breed-library-in-person = Библиотека не работает удалённо: за знанием надо прийти
breed-body-without-identity = тело без личности
breed-not-variety-seeds = «{ NAME($goods) }» — не семена сорта
breed-variety-gone = сорт этих семян исчез
breed-body-off-node = тело вне узла

# --- собирательство (engine/forage.py) ---------------------------------------

forage-nothing-here = на этой земле ничего не лежит: { $node } — голое место, а собирают то, что на нём растёт или из чего оно сложено
forage-nowhere-to-pour = { NAME($goods) } не во что набрать: жидкость живёт только в таре
forage-dead-gathers = мёртвое тело ничего не собирает
forage-already-searching = поиск уже идёт: дождитесь находки или закончите
forage-body-off-node = тело стоит в никуда
forage-not-your-land = чужая земля: что на ней лежит, принадлежит хозяину
forage-too-little-land = пустой земли { NUMBER($free, minimumFractionDigits: 0, maximumFractionDigits: 0) } м², а собирать есть где от { NUMBER($min, minimumFractionDigits: 0, maximumFractionDigits: 0) }
forage-no-strength = нет сил на поиск: нужно { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) }, есть { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) }
forage-not-searching = поиск не идёт: сначала начать
forage-still-searching = поиск не закончен: находка покажется — { $left }
forage-nothing-to-stop = собирательство не идёт: заканчивать нечего

# --- жидкости и тара (engine/liquid.py) --------------------------------------

liquid-dead-pours = мёртвое тело ничего не переливает
liquid-same-vessel = переливать в ту же тару незачем
liquid-not-a-vessel = «{ NAME($vessel) }» — не тара для жидкостей
liquid-body-off-node = тело вне узла
liquid-source-empty = { $named ->
        [true] в «{ NAME($vessel) }» нет «{ NAME($goods) }»
       *[false] «{ NAME($vessel) }» пуста
    }
liquid-nothing-to-pour = переливать нечего
liquid-no-room = в «{ NAME($vessel) }» свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг: не входит
liquid-vessel-not-here = «{ NAME($vessel) }» не в руках и не здесь
liquid-vessel-not-yours = «{ NAME($vessel) }» не ваша: тарой в узле распоряжается его хозяин
liquid-mixed = в «{ NAME($vessel) }» уже налито «{ NAME($have) }»: две жидкости в одну тару не смешивают

# --- линии борта (engine/ship/lines.py, D-288) --------------------------------

line-no-such-port = у «{ NAME($goods) }» такого порта нет
line-machine-not-aboard = «{ NAME($goods) }» на этом корабле не стоит: линию тянут от установленной машины
line-vessel-not-aboard = «{ NAME($goods) }» на этом корабле не стоит: на линии стоит только установленная тара

# --- воздух (engine/oxygen.py) -----------------------------------------------

oxygen-no-suit = в «{ $node }» нечем дышать: без «{ NAME($suit) }» из баллона не подышать, сколько бы их ни лежало в мешке
oxygen-tanks-empty = в «{ $node }» нечем дышать: в баллонах пусто, заправьтесь на борту
oxygen-not-enough = на дорогу в «{ $node }» нужно { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } кислорода, а в баллонах { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: переход кончится удушьем

# --- холод (engine/frost.py) -------------------------------------------------

frost-node-frozen = «{ $node }» промёрз: «{ NAME($station) }» здесь не работает. Тепло даёт «{ NAME($plant) }», «{ NAME($heater) }» или «{ NAME($brazier) }» с топливом
frost-dead-warms = мёртвое тело не греется
frost-asleep = тело спит: сначала проснуться
frost-not-a-warmer = «{ NAME($goods) }» не греет: для этого есть «{ NAME($warmer) }»
frost-warmer-from-hands = грелку достают из рук
frost-no-cold-here = здесь не мёрзнут: греться незачем, а грелка одноразовая
frost-reserve-full = теплозапас и так полон ({ NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) } ч из { NUMBER($ceiling, minimumFractionDigits: 1, maximumFractionDigits: 1) }): грелку берегут на холод

# --- энергия (engine/energy.py) ----------------------------------------------

energy-dead-loads = мёртвое тело ничего не грузит
energy-body-off-node = тело вне узла
energy-no-station = здесь нет станции, которой нужно топливо
# $fuel — список ключей через запятую: его разбирает NAMES().
energy-wrong-fuel = «{ NAME($goods) }» не горит в «{ NAME($station) }»: годится { NAMES($fuel) }
energy-fuel-from-hands = топливо грузят из рук
energy-nothing-to-load = грузить нечего
energy-no-grid = партия «{ NAME($goods) }» требует энергии, а городской сети здесь нет: вне города работают от аккумулятора
energy-pool-short = партия «{ NAME($goods) }» требует { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) } энергии, а в пуле { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) }: город без топлива стоит

# --- аккумуляторы (engine/battery.py) ----------------------------------------

battery-dead-charges = мёртвое тело не заряжает
battery-not-a-battery = «{ NAME($goods) }» — не аккумулятор: энергия в мешке не лежит
battery-body-off-node = тело вне узла
battery-not-here = аккумулятор не в руках и не стоит здесь
battery-no-grid = здесь нет городской сети: вне города работают от аккумулятора, и заряжают его в городе
battery-nothing-to-give = в пуле { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } энергии, а в аккумуляторе места на { NUMBER($place, minimumFractionDigits: 0, maximumFractionDigits: 0) }
battery-give-too-little = столько не перелить: энергия считается тысячными, и меньше { NUMBER($least, minimumFractionDigits: 3, maximumFractionDigits: 3) } не проходит

# --- забой (engine/mining.py) ------------------------------------------------

mining-vein-not-here = до жилы надо дойти ногами

# Полосы признака свода (D-143, D-303): пороги в реестре констант, слова здесь.
# Признак врёт на `mine.sign_noise` — но врёт число, а не слово.
mine-sign-roof-dry = свод сухой
mine-sign-roof-dust = сыплется пыль
mine-sign-roof-creaks = свод потрескивает
mine-sign-roof-cracking = трещит

mining-dead-works = мёртвое тело не работает
mining-vein-depleted = жила { $vein } выработана
mining-penal-face = каторжный забой работает только на заключённых
mining-no-strength = на удар нужно { NUMBER($need, minimumFractionDigits: 2, maximumFractionDigits: 2) } выносливости, а есть { NUMBER($have, minimumFractionDigits: 2, maximumFractionDigits: 2) }: сначала сон или обед
mining-session-open = у тела уже открыта сессия: в двух забоях сразу не бьют
mining-no-timber = нет шахтной крепи
# Крепь поднимает свод, а не задаёт его (D-188): выше своего потолка ей нечего
# поднимать, и поставленная там она только испортила бы выработку.
# Крепь — для свода, а завал разбирают ударами (D-301): подпорка, поставленная
# в завал, поднимала бы породу, а не свод.
mining-roof-buried = свод уже лёг: сначала разбирают завал
mining-roof-holds = свод держит сам: крепь ставят, когда он просядет
mining-session-without-body = сессия без тела
mining-session-closed = сессия { $session } закрыта: { $state ->
        [left] из забоя вышли
        [collapsed] свод обрушился
       *[active] забой ещё в работе
    }
mining-session-dangling = сессия ссылается в никуда
# Жидкая жила (D-252): кирке в ней не за что зацепиться.
mining-vein-liquid = «{ NAME($goods) }» киркой не взять: жидкую жилу качает буровая
# $names — список ключей: разбирает NAMES().
mining-no-tool = для добычи нужен инструмент класса «{ NAME($tool_class) }» ({ NAMES($names) }), а в руках его нет

# --- буровая (engine/rig.py) -------------------------------------------------

rig-dead-works = мёртвое тело не работает
rig-not-a-rig = «{ NAME($goods) }» — не буровая установка
rig-vein-not-here = жила не здесь: установку ставят на месте
rig-not-here = установка не здесь: бункер вывозят ногами
rig-not-yours = чужая установка: вывоз — по договору с хозяином
# Жидкий бункер (D-252): сливается только в тару, остаток ждёт в бункере.
rig-liquid-no-room = «{ NAME($goods) }» слить некуда: нужна тара со свободным местом — в руках или в узле

# --- автоматы (engine/automat.py) --------------------------------------------

auto-dead-works = мёртвое тело не работает
auto-not-an-automat = «{ NAME($goods) }» — не автомат
auto-not-installed = «{ NAME($goods) }» лежит, а не стоит: автомат работает установленным
auto-not-here = автомат стоит не здесь: программу загружают на месте
auto-not-entitled = автоматы программируют на своей земле
auto-recipe-unknown = рецепт «{ NAME($goods) }» вам неизвестен: автомат — не библиотека, сначала изучите его
auto-not-covered = «{ NAME($station) }» не по части автомата «{ NAME($goods) }»: у каждой станции свой автомат
auto-barred-input = «{ NAME($goods) }» не программируется: пироксисовый тир ждёт своей станции
auto-no-station-builds = «{ NAME($goods) }» — стройка: станции собирают руками, автомат их не строит
auto-body-off-node = тело вне узла
auto-link-self = «{ NAME($goods) }» сам себя не кормит: у провода два конца

# --- развалины Предтеч (engine/ruins.py) -------------------------------------

ruins-no-relic-of-class = в реестре нет реликвии класса «{ NAME($thing_class) }»
ruins-not-ruins = здесь нечего вскрывать: это не город Предтеч
ruins-exhausted = «{ $city }» выработан: вскрывать больше нечего
# `planets` нет среди доменов display_name, поэтому NAME() здесь была бы пустым
# обещанием: имя планеты едет как есть, ровно как до волны.
ruins-planet-without-node = у планеты «{ PLANET($planet) }» нет узла: миру нечего расширять

# --- сон (engine/rest.py) ----------------------------------------------------

rest-dead-sleeps = мёртвое тело не спит — оно мертво
rest-not-tired = выносливость полная: ложиться незачем
rest-not-sleeping = тело не спит

# --- смерть и печать тела (engine/death.py) ----------------------------------

death-body-alive = тело живо: второго одной личности не бывает
death-print-running = печать уже идёт
death-no-printer = в узле «{ $node }» нет биопринтера
death-print-queued = печать уже поставлена
death-no-grid = городской сети здесь нет: печать требует энергии, а её негде взять
death-pool-short = в пуле { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } энергии, а печать требует { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) }: город без топлива не печатает
death-no-iron = в принтере { NUMBER($have, minimumFractionDigits: 0, maximumFractionDigits: 0) } железа из { NUMBER($need, minimumFractionDigits: 0, maximumFractionDigits: 0) }: процессор не из чего собрать
death-prison-printer = тюремный принтер печатает только заключённых: это не дверь в мир
death-cannot-afford = печать стоит { $price } ₭, а на счету { $balance } ₭. Принтер Предтеч в столице печатает бесплатно — но двенадцать часов
death-job-dangling = печать { $job } ссылается в никуда

# --- разведка (engine/explore/) ----------------------------------------------

explore-unknown-goal = неизвестная цель поиска: { $goal }
explore-unknown-reach = поиск бывает ближним или дальним, «{ $reach }» — не про расстояние
explore-dead-scouts = мёртвое тело не разведывает
explore-no-such-ore = такой породы в этом мире не добывают: «{ NAME($resource) }»
explore-body-off-node = разведка идёт из узла, а тело стоит в никуда
explore-not-from-aboard = с борта не разведывают: под кораблём земли нет. Сойдите в порту и идите от него
explore-lot-only-in-city = участок ищут в городе: за стенами городской застройки нет
# Что здесь можно искать: каждая цель называет своё слово, а склеивает их
# язык (`inner`). Раньше это была карта из пяти русских существительных в
# винительном падеже, приваренных к одной этой фразе.
explore-goal-lot = участок
explore-goal-site = новое место
explore-goal-vein = жилу
explore-goal-forest = лес
explore-goal-room = помещения Предтеч
explore-wrong-goal-here = отсюда так не ищут: здесь ищут { $offers ->
        [none] ничего
       *[some] { $words }
    }
explore-city-exhausted = «{ $city }» выработан: всё, что можно было вскрыть, уже вскрыто
explore-no-strength = на заход нужно { NUMBER($need, minimumFractionDigits: 1, maximumFractionDigits: 1) } выносливости, а есть { NUMBER($have, minimumFractionDigits: 1, maximumFractionDigits: 1) }: берут по самому долгому заходу отсюда — сначала поесть или поспать
explore-already-out = заход уже идёт: дождитесь возвращения
explore-run-queued = заход уже поставлен
explore-run-dangling = заход { $job } ссылается в никуда
explore-not-out = тело не в разведке: возвращаться неоткуда
explore-lot-outside-city = участок ищут в городе: за стенами застройки нет
