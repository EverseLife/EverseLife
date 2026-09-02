# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Деньги: маркетплейс, банк, переводы, госзаказ, таможня, коммуналка
# (D-047, D-190, D-248, D-251).
#
# NAME($id) превращает устойчивый ключ вещи или станции в слово этого языка:
# по проводу едет `iron_ore`, читатель видит «Железная руда».
#
# Суммы приходят уже собранными: их пишет money_str() по D-190, и переписывать
# это правило в FTL нельзя — в сообщение подставляется готовая строка.
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.

## Маркетплейс

market-no-terminal = в узле { $node } нет терминала маркетплейса
market-nothing-free = свободного «{ NAME($goods) }» в терминале нет: всё под ордерами
# Бак терминала (D-255): жидкость торгуется из него и в него.
market-tank-full = бак терминала полон: «{ NAME($goods) }» некуда лить, пока кто-нибудь не выкупит
market-liquid-no-room = «{ NAME($goods) }» не во что слить: купленное ждёт в баке, приходите с тарой
market-nothing-loaded = в вашей таре нет «{ NAME($goods) }»: жидкость наливают из своей тары
market-not-enough-free = в терминале свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } «{ NAME($goods) }» ступени «{ TIER($tier) }», нужно { NUMBER($quantity, minimumFractionDigits: 1, maximumFractionDigits: 1) }
market-dead-trades = мёртвое тело не торгует
market-body-without-identity = тело без личности
market-not-enough-money = на счету столько нет: заявка стоит { $money } ₭
market-price-not-positive = цена должна быть положительной
market-volume-not-positive = объём должен быть положительным
market-body-off-node = тело вне узла
market-order-off-node = ордер вне узла
market-node-city-missing = узел { $node } принадлежит несуществующему городу
market-no-such-tier = нет такой ступени качества: «{ TIER($tier) }»
market-floor-not-in-tier = качество { $floor } — это ступень «{ TIER($floor_tier) }», а заявка выписана на «{ TIER($tier) }»
market-floor-off-scale = качество называют числом от { $frm } до { $to }, а не { $floor }
market-no-such-goods = в мире нет такого товара: «{ NAME($goods) }»
market-goods-relic = «{ NAME($goods) }» — наследие Предтеч: такое не делают и не переносят
market-no-such-recipe = в мире нет такого рецепта: «{ NAME($recipe) }»

market-reserve-not-a-sale = бронируют товар, а не заявку на покупку
market-order-not-active = заявка уже { $state ->
        [filled] исполнена
        [cancelled] снята
        [expired] просрочена
       *[active] в работе
    }
market-reserve-own = свой товар бронировать незачем: он и так ваш
market-reserve-zero = бронь из нуля
market-reserve-too-much = в заявке свободно { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) }, а брони просят { NUMBER($quantity, minimumFractionDigits: 1, maximumFractionDigits: 1) }
market-reservation-not-yours = чужая бронь
market-reservation-not-held = бронь уже { $state ->
        [redeemed] выкуплена
        [lapsed] просрочена
       *[held] держится
    }
market-reservation-elsewhere = бронь не здесь: за товаром приезжают
market-reservation-expired = срок брони вышел: задаток остался продавцу
market-goods-vanished-reservation = товар исчез из терминала между бронью и выкупом
market-goods-vanished-trade = товар исчез из терминала между проверкой и сделкой

market-order-not-yours = чужой ордер
market-order-already = ордер уже { $state ->
        [filled] исполнен
        [cancelled] снят
        [expired] просрочен
       *[active] в работе
    }
market-job-no-reservation = задание { $job }: брони нет
market-job-no-order = задание { $job }: ордера нет

## Банк

bank-loan-not-positive = заём должен быть положительным
bank-no-citizenship = занять можно только у города своего гражданства, а вы нигде не состоите (D-281)
bank-city-cannot-fund = у города «{ $city }» нет на это денег: в казне { $own } ₭, занять у столицы он может ещё { $free } ₭. Кредит выдаёт город из своей казны (D-283)
bank-over-limit = столько не дают: доступно { $available } ₭ из лимита { $limit } ₭ ({ $reason })
bank-loan-closed = этот заём уже закрыт
bank-nothing-to-pay-with = платить нечем
bank-council-not-yet = ставку решает алгоритм: городов с администрацией меньше { NUMBER($cities, maximumFractionDigits: 2) } либо действует блокировка
bank-out-of-corridor = алгоритм рекомендует { NUMBER($recommendation, minimumFractionDigits: 2, maximumFractionDigits: 2) }%, отклониться можно на { NUMBER($corridor, maximumFractionDigits: 2) } п.п. — просят { NUMBER($rate, minimumFractionDigits: 2, maximumFractionDigits: 2) }%
bank-complain-about-self = на себя не жалуются даже по лору

## Объяснение банка: ставка, лимит, ваша ставка (D-030, D-173, D-193)
#
# Ставка и лимит считаются по открытой формуле, и формула объясняется словами:
# иначе с денежной политикой не с чем спорить. Каждая оговорка — отдельное
# сообщение, а как их сцепить в одну строку, решает язык (i18n.join).
#
# Знак «+» перед числом ставит сам текст: NUMBER() показывать его не умеет
# (signDisplay в fluent.runtime нет), а «+0,50» говорит, в какую сторону
# двинулся рычаг. Движок передаёт флаг, знак дописывает язык.

bank-why-rate-base = база { NUMBER($rate, maximumFractionDigits: 2) }
bank-why-rate-inflation = инфляция { $inflation_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($inflation, minimumFractionDigits: 1, maximumFractionDigits: 1) } против цели { NUMBER($goal, maximumFractionDigits: 2) } → { $bonus_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($bonus, minimumFractionDigits: 2, maximumFractionDigits: 2) }
bank-why-rate-inflation-unknown = инфляция не измерена: реакции нет
bank-why-rate-emission = эмиссия { NUMBER($share, maximumFractionDigits: 0) }% против цели { NUMBER($goal, maximumFractionDigits: 2) } → { $bonus_up ->
        [true] +
       *[false] {""}
    }{ NUMBER($bonus, minimumFractionDigits: 2, maximumFractionDigits: 2) }
bank-why-council = решение Совета городов ({ $city }); алгоритм советовал { NUMBER($advised, minimumFractionDigits: 2, maximumFractionDigits: 2) }

bank-why-limit-base = база { $money }
bank-why-limit-turnover = оборот { $money } за { NUMBER($days, maximumFractionDigits: 2) } суток
bank-why-limit-interest = уплачено процентов { $money }
bank-why-limit-no-overdue = стаж без просрочек
bank-why-limit-trust = доверие { NUMBER($trust, maximumFractionDigits: 0) }% по репортам

bank-why-offer-key = ключевая { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }%
bank-why-offer-no-citizenship = кредита нет: занимают только у города своего гражданства, а вы нигде не состоите (D-281)
bank-why-offer-city = ключевая { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }% + маржа города { NUMBER($margin, minimumFractionDigits: 2, maximumFractionDigits: 2) }% ({ $city }); линии свободно { $free } ₭
bank-why-offer-line-exhausted = ключевая { NUMBER($key, minimumFractionDigits: 2, maximumFractionDigits: 2) }% + маржа { NUMBER($margin, minimumFractionDigits: 2, maximumFractionDigits: 2) }%, но занять сейчас нечего: линия города «{ $city }» исчерпана — разрешено { $permitted } ₭ от оборота, свободно { $free } ₭. Линию поднимают сделки на его земле (D-193)

## Переводы

finance-zero-transfer = перевод на ноль — не перевод
finance-memo-too-long = основание длиннее { $limit } знаков
finance-no-such-payee = нет такой личности: «{ $payee }»
finance-self-transfer = перевод самому себе ничего не меняет
finance-not-enough-money = на счету столько нет

## Городской госзаказ

works-city-offer-negative = предложение города не бывает отрицательным
works-city-no-labor = заказ без труда — это не заказ
works-city-order-exists = на этом объекте уже висит такой заказ
works-city-fund-empty = фонд работ пуст: не хватает { $money } ₭ на долю фонда. Фонд наполняется процентным доходом — подождите
works-city-treasury-poor = в казне не хватает { $money } ₭ на долю города

works-city-repair-not-own = город заказывает ремонт своего: этот участок не его
works-city-nothing-to-repair = чинить нечего: дома целы либо их нет
works-city-build-not-own = город заказывает стройку у себя: этот участок не его
works-city-unknown-building = тип «{ NAME($building) }» этому миру неизвестен
works-city-no-footprint = дом без пятна или без этажей — это не заказ

works-city-station-not-in-city = станция не на территории города: возить туда город не заказывает
works-city-no-station = здесь нет станции, которой нужно топливо
works-city-not-a-fuel = «{ NAME($goods) }» не горит в «{ NAME($station) }»
works-city-zero-haul = подвоз нуля — это не заказ

works-city-no-such-order = такого заказа у города нет
works-city-order-closed = заказ уже закрыт: отзывать нечего
works-city-work-under-way = работа по заказу уже идёт: работник вложил материалы — дождитесь конца

works-city-loan-not-positive = заём должен быть положительным
works-city-capital-prints = столица у самой себя не занимает: «{ $city }» печатает деньги по подписям держателей права
works-city-line-exhausted = линия города исчерпана: свободно { $money } ₭ из { $permitted } ₭. Линию поднимают оборот на земле города и уплаченный им процент (D-285)
works-city-not-treasury-loan = это не заём казны этого города

## Таможня

customs-banned = «{ NAME($goods) }» не проходит границу города «{ $city }»: { $direction ->
        [import] ввоз
       *[export] вывоз
    } запрещён
customs-cannot-pay = пошлина { $duty } ₭, а на счету { $have } ₭: товар не проходит. Долга при этом не возникает

## Коммуналка

utility-node-not-yours = узел не ваш: чужие счета оплачивает договор, а не движок
utility-nothing-due = долга нет
utility-no-grid = здесь нет городской сети
utility-not-enough-money = долг { $debt } ₭, а на счету { $have } ₭

## Выписка: кто на той стороне проводки и на каком основании

# Сторона проводки, у которой нет имени человека. Вид счёта приходит с
# провода как есть (`genesis`, `bank_reserve`), словом становится здесь —
# иначе игрок читает в выписке `works_fund`, как и читал до этой волны.

ledger-side-city_treasury = { $named ->
        [true] казна: { $name }
       *[false] казна города
    }
ledger-side-genesis = эмиссия
ledger-side-bank_reserve = резерв банка
ledger-side-works_fund = фонд работ
ledger-side-escrow = залог сделки
ledger-side-identity = человек

# Основание проводки: тот же перечень, что и `PostingReason`. До этой волны
# перечень жил на клиенте отдельной картой и расходился с сервером — новое
# основание показывалось игроку своим кодом.

ledger-ground-genesis = эмиссия
ledger-ground-trade = сделка
ledger-ground-tax_trade = налог с продажи
ledger-ground-market_fee = сбор рынка
ledger-ground-duty = пошлина
ledger-ground-salary = жалованье
ledger-ground-tax_land = земельный налог
ledger-ground-energy_bill = энергия
ledger-ground-court_fee = пошлина суда
ledger-ground-fine = штраф
ledger-ground-escrow_hold = задаток
ledger-ground-escrow_release = возврат задатка
ledger-ground-loan = кредит
ledger-ground-loan_repayment = погашение
ledger-ground-seigniorage = сеньораж
ledger-ground-bank_margin = маржа города
ledger-ground-transfer = перевод
ledger-ground-works_recycle = возврат в фонд работ
ledger-ground-works_print = печать в фонд работ
ledger-ground-works_payout = оплата госзаказа
ledger-ground-emission = эмиссия по подписям столицы
