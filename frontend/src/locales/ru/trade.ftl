# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова окон, где меняют вещи на деньги и вещи на вещи: рынок, рабочая
# станция, инвентарь, банк, финансы, монетная станция, биопринтер, очаг,
# библиотека и поле пароля (D-251, волна IV).
#
# Правила те же, что у `ui.ftl`: значение — одной строкой, каким бы длинным
# оно ни было (перенос попал бы в текст); варианты выбора — каждый на своей
# строке, эти переносы в текст не попадают.

## Рынок

ui-market-title = Рынок
ui-market-search = найти товар
ui-market-search-label = поиск товара
ui-market-found = { $found } из { $all }
ui-market-hint = торгуется здесь и лежит у вас; ищите — найдётся любое
ui-market-none-found = ничего не нашлось
ui-market-none-traded = здесь ещё ничем не торговали
ui-market-quality = качество
ui-market-stock = в терминале { $shelf } · в руках { $hand }
ui-market-bids = покупают
ui-market-price = цена ₭
ui-market-asks = продают
ui-market-rung = цена { $price } за единицу
ui-market-book-empty = по этой позиции стакан пуст: цену назначает первый
ui-market-last = последняя сделка: { $price } ₭
ui-market-volume = сколько
ui-market-volume-unit = сколько, { $unit }
ui-market-price-each = цена за единицу, ₭
ui-market-total = итого
ui-market-tax = налог платит продавец
ui-market-buy = Купить
ui-market-buy-hint = встать в стакан со своей ценой; что дешевле — купится сразу
ui-market-sell = Продать
ui-market-sell-hint = встать в стакан со своей ценой; что дороже — продастся сразу
ui-market-sell-none = продавать нечего: товар должен лежать в терминале
ui-market-buy-best = По рынку купить
ui-market-buy-best-at = По рынку купить · { $price } ₭
ui-market-buy-best-hint = купить по лучшей цене продавцов
ui-market-sell-best = По рынку продать
ui-market-sell-best-at = По рынку продать · { $price } ₭
ui-market-sell-best-hint = продать по лучшей цене покупателей; товар должен лежать в терминале
ui-market-rest = Остаток заявки встаёт ордером и ждёт. Покупают стоя здесь; свои ордера — в «торговле».
ui-market-reserve-title = Забронировать
ui-market-reserve-rule = Бронируют издалека, забирают ногами; не забрал в срок — задаток у продавца.
ui-market-reserve = Бронь
ui-market-reserve-hint = внести задаток и забрать до срока
ui-market-reservation = бронь: { $goods } · { $amount } по { $price } ₭
ui-market-redeem = Выкупить
ui-market-terminal = Терминал
ui-market-terminal-rule = Продаётся то, что в терминале; купленное забирается отсюда же. Клик по строке выбирает позицию. Перетащите сюда строку из инвентаря, чтобы выложить, и обратно в инвентарь — чтобы забрать.
ui-market-terminal-drop = перетащите сюда предмет из инвентаря, чтобы выложить
ui-market-terminal-empty = в терминале ничего вашего
ui-market-row = позиция { $goods }, { $tier }
ui-market-take = Забрать

## Рабочая станция

ui-workshop-by-hand = Руками
ui-workshop-rule = Партия идёт, только пока вы стоите здесь: ушли — замерла, вернулись — продолжилась. У одного человека идёт одна работа, остальные ждут очереди в «делах». За рабочей станцией работает один.
ui-workshop-cut-off = Узел отключён за неуплату: рабочие станции не работают, пока долг не закрыт. Счёт — в сайдбаре, во вкладке «хозяйство».
ui-workshop-station-quality = качество { $quality }
ui-workshop-station-condition = состояние { $condition }
ui-workshop-station-busy-mine = занята вами
ui-workshop-station-busy-other = занята другим
ui-workshop-station-free = свободна
ui-workshop-station-take = Забрать
ui-workshop-station-take-hint = забрать станцию в руки
ui-workshop-auto = на автомате
ui-workshop-input = { $goods } · в руках { $amount }
ui-workshop-write = записать рецепт:
ui-workshop-write-nothing = вы пока ничего не знаете, кроме самого носителя
ui-workshop-quality = качество
ui-workshop-ceiling-hint = потолок станка: { $ceiling }
ui-workshop-seconds = с
ui-workshop-minutes = мин
ui-workshop-waste = потери
ui-workshop-ceiling = потолок
ui-workshop-consumes = уйдёт:
ui-workshop-energy = энергии { $energy } на { $cost } ₭ по тарифу города
ui-workshop-forecast = Прогноз считается сам, пока вы выбираете.
ui-workshop-queue = В очередь
ui-workshop-start = Запустить партию
ui-workshop-running = сейчас идёт «{ $goods }»: новая партия встанет за ней
ui-workshop-repair-title = Починить или разобрать
ui-workshop-thing-condition = { $goods } · состояние { $condition }
ui-workshop-repair = Починить
ui-workshop-recycle = Разобрать
ui-workshop-invent-title = Без рецепта
ui-workshop-invent-rule = Выложите состав на единицу изделия — до { $cap } видов вещей из рук — и сколько единиц делаете. Совпало с тем, что здесь делают, — рецепт ваш и партия пошла. Не совпало — сгорает случайная часть выложенного: цена попытки. Подсказок «теплее — холоднее» нет.
ui-workshop-invent-empty = В руках пусто: выкладывать нечего.
ui-workshop-invent-per-unit = сколько на единицу изделия
ui-workshop-invent-drop = убрать
ui-workshop-invent-add = + вещь
ui-workshop-invent-units = единиц
ui-workshop-invent-try = Попробовать
ui-workshop-invent-done = Сложилось: «{ $learned }» теперь в ваших знаниях.
ui-workshop-invent-done-batch = Сложилось: «{ $learned }» теперь в ваших знаниях — и первая партия пошла.
ui-workshop-invent-burned = Сгорело: { $burned }.

## Инвентарь

ui-inventory-carry = в руках { $load } из { $capacity } кг
ui-inventory-carry-rule = Смотреть можно откуда угодно, есть — из рук и в дороге тоже, а трогать остальное только ногами. Передают из рук в руки: оба человека стоят в одном месте, и передача видна остальным — в разговоре появляется строка о ней. Полные руки посылку не примут: предел носимого чужой тоже.
ui-inventory-slot-empty = пусто
ui-inventory-unequip = снять
ui-inventory-group = сгруппировать
ui-inventory-sort = упорядочить
ui-inventory-desc = по убыванию — нажмите для возрастания
ui-inventory-asc = по возрастанию — нажмите для убывания
ui-inventory-drop-hint = перетащите сюда предмет, чтобы взять в руки
ui-inventory-empty = В руках ничего нет.
ui-inventory-menu = что можно с «{ $goods }»
ui-inventory-amount = сколько
ui-inventory-equip = Надеть
ui-inventory-eat = Съесть
ui-inventory-warm = Согреться
ui-inventory-warm-hint = сломать грелку: часы теплозапаса сразу, сверх потолка не копятся
ui-inventory-copy = Скопировать в знания
ui-inventory-copy-hint = скопировать рецепт в знания: стоит выносливости, носитель цел
ui-inventory-copy-known = этот рецепт уже в личности
ui-inventory-wipe = Стереть
ui-inventory-wipe-hint = стереть запись: останется болванка
ui-inventory-put = Положить…
ui-inventory-hand = Передать…
ui-inventory-where = Куда положить · { $amount }
ui-inventory-floor = На пол
ui-inventory-ground = На землю
ui-inventory-not-yours = Земля чужая: положенное здесь достанется хозяину, и обратно вы его не возьмёте.
ui-inventory-in-hands = { $goods } в руках
ui-inventory-pour = Перелить в { $target }
ui-inventory-pour-hint = перелить всё, что внутри, сколько войдёт
ui-inventory-into = В { $chest }
ui-inventory-contribute = В библиотеку
ui-inventory-contribute-hint = отдать в библиотеку навсегда: ваше имя останется при рецепте
ui-inventory-contribute-there = этот рецепт здесь уже лежит
ui-inventory-hold = В трюм
ui-inventory-terminal = В терминал
ui-inventory-terminal-hint = выложить в терминал: продаётся то, что в нём лежит
ui-inventory-cancel = Отмена
ui-inventory-whom = Кому передать · { $amount }
ui-inventory-nobody = Здесь никого больше нет: передают из рук в руки.
ui-inventory-on-terminal = В терминале
ui-inventory-average = в среднем { $quality }
ui-inventory-mass = { $mass } кг
# Два аргумента на одно число: `$count` выбирает форму слова (это умеет только
# число), `$shown` — те самые цифры, что уже выбрала панель. Само `{ $count }`
# в тексте Fluent отформатировал бы по правилам языка, и «1000 позиций» стало
# бы «1 000 позиций» — иначе, чем остальные числа того же окна.
ui-inventory-positions = { $count ->
    [one] { $shown } позиция
    [few] { $shown } позиции
   *[many] { $shown } позиций
  }
ui-inventory-fineness = проба { $fineness }
ui-inventory-maker = клеймо { $maker }
ui-inventory-variety = сорт
ui-inventory-vigor = { $variety } · сила { $vigor }
ui-inventory-charge = заряд { $charge }
ui-inventory-condition = сост. { $condition }
ui-inventory-spoiled = испортилось
ui-inventory-spoils = испортится через { $hours } ч
ui-inventory-keeps = годно { $days } сут.

## Банк

ui-bank-title = Банк
ui-bank-rule = Залога нет: лимит выдаёт труд — оборот и уже погашенные кредиты. Занимает вам ваш город со своей маржой, и пока его линия не исчерпана, ставка ниже: дальше деньги идут напрямую у столицы, с надбавкой за риск.
ui-bank-rate = ключевая ставка
ui-bank-circulating = в обороте
ui-bank-reserve = в резерве
ui-bank-fund = в фонде работ
ui-bank-debts = Ваши долги
ui-bank-outstanding = осталось вернуть
ui-bank-loan = из { $principal } ₭ под { $rate }% · взят { $taken }
ui-bank-repay = Погасить
ui-bank-borrow-title = Занять
ui-bank-limit = ваш лимит
ui-bank-your-rate = вам дадут под
ui-bank-amount = сколько занять, ₭
ui-bank-borrow = Взять кредит
ui-bank-works = Госзаказ
ui-bank-order-road-mend = обслуживание дороги
ui-bank-order-building-repair = ремонт постройки
ui-bank-order-building-build = стройка
ui-bank-order-fuel-delivery = подвоз топлива
ui-bank-fuel-left = { $goods }: осталось { $left }
ui-bank-building = { $kind }, { $footprint } м², этажей { $floors }
ui-bank-council = Совет городов
ui-bank-council-locked = ставка возвращена алгоритму ещё на { $left }: инфляция за тревожной чертой
ui-bank-council-waiting = ставку считает алгоритм: городов с администрацией { $cities } из { $needed }, дальше решает Совет городов
ui-bank-council-rate = ставка города, % — коридор ±{ $corridor } вокруг { $advised }%
ui-bank-council-vote = Голос города за ставку
ui-bank-council-advises = алгоритм советует { $advised }%
ui-bank-council-corridor = коридор ±{ $corridor }: Совет спорит с алгоритмом, а не заменяет его
ui-bank-council-voter = голос подаёт держатель права «законы»

## Финансы

ui-finance-account = Счёт
ui-finance-account-rule = Счёт переживает смерть тела: деньги в Сети, а не в кармане.
ui-finance-transfer-title = Перевести
ui-finance-transfer-rule = Перевод идёт без комиссии и налога — и отменить его нельзя. Основание видят получатель и суд: это единственное, что останется от сделки, если о ней придётся спорить.
ui-finance-to = кому
ui-finance-to-hint = имя личности
ui-finance-amount = сколько, ₭
ui-finance-memo = за что
ui-finance-memo-hint = видно получателю и суду
ui-finance-transfer = Перевести
ui-finance-statement = Выписка
ui-finance-none = операций пока нет

## Монетная станция

ui-mint-title = Монетная станция
ui-mint-nothing = Чеканить нечего: рецепт монеты берут в Библиотеке. Монета — предмет, и делается она как всякий предмет, только своей дверью.
ui-mint-count = сколько монет
ui-mint-fineness = проба { $fineness } ‰ — одна на весь мир
ui-mint-cost = Уйдёт { $metal } «{ $metalName }» (в руках { $metalHave }) и { $iron } «{ $ironName }» (в руках { $ironHave }). Лигатура — десятая часть железа: монета всегда 900-й пробы.
ui-mint-strike = Чеканить
ui-mint-not-enough = металла или железа не хватает: партия не начнётся
ui-mint-purse = Кошелёк
ui-mint-purse-rule = Переплавка вернёт аффинированный металл за вычетом угара; лигатура теряется — выковыривать её дороже самого железа.
ui-mint-row-fineness = проба { $fineness }
ui-mint-row-maker = клеймо { $maker }
ui-mint-melt = Переплавить

## Биопринтер

ui-printer-title = Тела нет
ui-printer-rule = Город продаёт не жизнь, а скорость: заплатил — вернулся через минуты, не заплатил — через двенадцать часов у Принтера Предтеч. Поэтому у цены воскрешения есть потолок, и никто не может запереть личность у себя.
ui-printer-note = Личность цела: имя, знания, счёт и обязательства пережили тело. Погибло то, что тело несло, — и треть этого осталась лежать на месте гибели.
ui-printer-printing = печать идёт · тело будет { $when }
ui-printer-none = В мире нет ни одного биопринтера. Это ситуация, которой быть не должно: вход в игру не блокируется никогда.
ui-printer-precursor = Предтечи
ui-printer-free = бесплатно
ui-printer-at-city-expense = за счёт города
ui-printer-no-cost = энергии и железа не требует
ui-printer-energy = энергии
ui-printer-iron = железа
ui-printer-enough = { $needed } { $what }
ui-printer-short = { $what } { $here } из { $needed }
ui-printer-print = Печатать
ui-printer-term-minutes = { $minutes } мин
ui-printer-term-hours = { $hours } ч
ui-printer-soon = вот-вот
ui-printer-in-minutes = через { $minutes } мин
ui-printer-in-hours = через { $hours } ч

## Очаг

ui-kitchen-title = Очаг
ui-kitchen-rule = Пустая роль режет качество сильнее плохого продукта. Сочетание решает вид блюда — по видам считается разнообразие рациона. Нужна утварь в кармане: горшок или котёл.
ui-kitchen-none = Ни одного блюда в личности: рецепты берут в Библиотеке.
ui-kitchen-whole = котёл варится целиком
ui-kitchen-empty = — пусто —
ui-kitchen-cook = Сварить котёл

## Библиотека

ui-library-title = Библиотека
ui-library-rule = Бесплатно и без условий, но только придя; переписывание стоит выносливости. Здесь лежит то, что сюда положили: столичная полна с основания, городскую наполняют носителями — из инвентаря, «Положить… → В библиотеку». Положенное остаётся навсегда, имя вкладчика — при рецепте.
ui-library-search = рецепт, станция, вход или вкладчик
ui-library-found = { $found } из { $all }
ui-library-shelf-empty = Полки пусты: эта библиотека ещё ничего не получила. Принесите носитель «Рецепт» и положите его сюда из инвентаря.
ui-library-recipe = рецепт
ui-library-level = ур.
ui-library-station = станция
ui-library-inputs = из чего
ui-library-contribution = вклад
ui-library-founding = основание
ui-library-known = знаю
ui-library-take = Взять
ui-library-none-found = ничего не нашлось
ui-library-page = страница { $page } из { $pages }
ui-library-carriers = Носители в руках
ui-library-already = здесь уже лежит
ui-library-give = Положить в библиотеку
ui-library-give-hint = отдать навсегда: ваше имя останется при рецепте
ui-library-agrotech = Агротехника
ui-library-agrotech-known = агротехника уже в личности
ui-library-agrotech-hint = взять норму культуры: бесплатно, навсегда
ui-library-agrotech-note = Агротехника базовых культур — для всех: с ней грядка показывает норму, а не симптом. Взятое помечено ✓.

## Поле пароля

ui-secret-password = пароль
ui-secret-hide = скрыть
ui-secret-hide-label = скрыть пароль
ui-secret-show = показать
ui-secret-show-label = показать пароль
