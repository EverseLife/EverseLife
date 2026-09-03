# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Слова окон города и входа в мир: администрация, госзаказ, казна, население,
# двери, регистрация, вход, первое слово (D-251, волна IV).
#
# Правила те же, что у `ui.ftl`: значение — одной строкой, каким бы длинным оно
# ни было (перенос попал бы в текст); варианты выбора — каждый на своей строке.
#
# Числа приезжают сюда строками там, где экран печатал их как есть: `{ $n }` с
# настоящим числом Fluent отформатировал бы по правилам языка — «1 234» вместо
# «1234», — и строка изменилась бы молча.

## Вход (D-187)

ui-login-alpha = альфа
ui-login-title = Войти
ui-login-email = почта
ui-login-password = пароль
ui-login-submit = Войти
ui-login-no-account = Ещё нет аккаунта?
ui-login-register = Регистрация

## Слово при пробуждении (D-182)

ui-intro-title = Вы открыли глаза
ui-intro-forerunners = Настоящие люди — Предтечи — построили машину, которая печатает людей, и исчезли. Их города лежат подо льдом, их руины — под столицей; живых свидетелей не осталось ни одного.
ui-intro-machine = Машина работает до сих пор. Тело, в котором вы стоите, собрано ею минуту назад: мышление держит процессор, а имя, знания и счёт хранит Сеть. Поэтому смерть здесь отнимает вещи, но не вас.
ui-intro-legacy = Наследство досталось целым и пустым: чертежи есть, руда в земле, дорог нет. Продолжать дело Предтечей больше некому — кроме вас и таких же напечатанных. Всё, что появится в этом мире, сделают люди.
ui-intro-start = С чего начать
ui-intro-step-look = Осмотреться в узле и собрать простое: камень, хворост, волокно. Действие — предмет, и это первое, что стоит проверить руками.
ui-intro-step-recipe = Взять первый рецепт в Библиотеке столицы. Знаний вам не выдали никаких: ремесло — это знание, и за ним надо прийти.
ui-intro-step-sell = Продать сделанное на терминале. Цену здесь назначают люди, а не мир: первый заработок и есть первая встреча с ними.
ui-intro-go = Начать
ui-intro-again = Открыть снова — знак «?» в шапке, а на телефоне — «кто вы» в меню «ещё».

## Регистрация в четыре шага (D-187)

ui-register-step-account = аккаунт
ui-register-step-line = линия
ui-register-step-character = персонаж
ui-register-step-city = город
ui-register-steps-label = шаги регистрации
ui-register-bad-email = почта выглядит неправильно
ui-register-short-password = пароль короче { $min } знаков
ui-register-password-mismatch = пароли не совпадают
ui-register-no-name = имя не названо
ui-register-long-name = имя длиннее { $limit } знаков
ui-register-long-surname = фамилия длиннее { $limit } знаков
ui-register-age-range = возраст от { $min } до { $max }
ui-register-long-about = описание длиннее { $limit } знаков
ui-register-account = Аккаунт
ui-register-email = почта
ui-register-password = пароль
ui-register-password-hint = не короче { $min } знаков
ui-register-again = ещё раз
ui-register-again-hint = повторите пароль
ui-register-to-login = ← ко входу
ui-register-next = Дальше →
ui-register-line = Линия
ui-register-line-note = Кем вы напечатаны. В альфе играбельна одна линия; вторая видна как обещание, а не заглушка.
ui-register-line-players = играют
ui-register-line-world = мир
ui-register-pick = Выбрать
ui-register-soon = Ещё в разработке
ui-register-back = ← назад
ui-register-character = Персонаж
ui-register-name = имя
ui-register-name-hint = как вас будут звать
ui-register-name-note = Имя уникально и не меняется никогда: на нём держится репутация. Всё остальное можно поправить потом в кабинете.
ui-register-surname = фамилия
ui-register-age = возраст
ui-register-about = описание
ui-register-about-hint = внешность, характер, откуда родом — как хотите

## Где напечататься в первый раз (D-013, D-182, D-184)

ui-doors-title = Где вас напечатать
ui-doors-lead = { $name }, тела у вас ещё нет — есть выбор машины, которая его соберёт. Первое тело печатается сразу и бесплатно везде; дальше за скорость платят.
ui-doors-search = найти город
ui-doors-search-label = поиск города
ui-doors-count = { $shown } из { $total } · сортировка по людям в городе
ui-doors-empty-world = В мире нет ни одного биопринтера. Этого положения быть не должно: вход в игру не блокируется никогда.
ui-doors-nothing-found = Ничего не нашлось — попробуйте иначе.
ui-doors-precursor = Принтер Предтеч
ui-doors-precursor-note = Вечная машина настоящих людей: ничьей казны не требует и не откажет никому.
ui-doors-city-note = Городской биопринтер: работает на энергии и железе города.
ui-doors-city = город
ui-doors-outside = вне города
ui-doors-people = людей сейчас
ui-doors-citizens = граждан
ui-doors-grant = подъёмные
ui-doors-nothing = нет
ui-doors-first-body = первое тело
ui-doors-at-once = сразу
ui-doors-citizenship = гражданство
ui-doors-citizenship-at-once = сразу
ui-doors-tax = налог с продажи
ui-doors-print-here = Печататься здесь
ui-doors-grant-note = Подъёмные платит город из своей казны, а не мир из воздуха: новый житель городу выгоден, и потому за него торгуются.
ui-doors-rules-note = Строки таблицы движок исполняет: гражданство города наступает в момент печати и ничем не держит — выйти можно хоть в первую минуту, пока не взят кредит; налог удерживается с каждой продажи. Принтер Предтеч записывает в город, на чьей земле стоит: машина ничья, человек из неё — чей-то.
ui-doors-word-note = В кавычках — слово самого города. Это обещание живых людей, и движок за него не отвечает: не сдержали — дело суда.
ui-doors-back = ← назад

## Вкладка государства: экономика и население (D-124, D-140, D-154)

ui-city-asking = Город опрашивается…
ui-city-outside = Вы вне города: за стенами законов нет.
ui-city-silent = Город не ответил: о нём сейчас ничего не известно.
ui-city-again = Ещё раз
ui-city-recount = Пересчитать
ui-city-treasury-sign = { $city } · казна { $treasury } ₭
ui-city-money = Деньги мира
ui-city-money-total = масса ₭
ui-city-money-median = медиана счёта
ui-city-money-gini = неравенство (Джини)
ui-city-trades = сделок за сутки
ui-city-prices = Цены за сутки
ui-city-laws = По каким правилам живём
ui-city-laws-rule = Менять законы — в администрации: власть присутственна. Вкладка видна только должностям: это цифры, которыми правят.
ui-city-law-own = решение города
ui-city-law-default = умолчание
ui-city-people-world = личностей в мире
ui-city-people-here = тел в городе
ui-city-people-printed = напечатано за окно
ui-city-offices = Должности
ui-city-offices-rule = Назначать и снимать — в администрации: власть присутственна.
ui-city-offices-none = должностей нет
ui-city-residents = Жители
ui-city-residents-none = пока никого
ui-city-report-who = имя дефектной печати
ui-city-report = Сообщить
ui-city-unreport = Отозвать
ui-city-unreport-title = отозвать свой репорт
ui-city-report-note = Репорт снижает доверие и кредитный лимит цели — не больше того. Ошиблись — отзовите.

## Госзаказ и кредит казне (D-248)

ui-city-works-repair = ремонт постройки
ui-city-works-build = стройка
ui-city-works-fuel = подвоз топлива
ui-city-works-title = Госзаказ
ui-city-works-rule = Город называет работу и свою цену за нетрудовое — материалы, топливо; фонд работ доплачивает долю трудового тарифа. Деньги откладываются при вывеске: пустая казна или пустой фонд откажут сразу. Заказ — это и лицензия: пока он висит, чинить и строить на участке города может любой.
ui-city-works-cancel = Отозвать
ui-city-works-node = участок (ключ узла)
ui-city-works-offer = предложение города, ₭ — за материалы или топливо
ui-city-works-order-repair = Заказать ремонт
ui-city-works-kind = тип дома
ui-city-works-footprint = пятно, м²
ui-city-works-floors = этажей
ui-city-works-order-build = Заказать стройку
ui-city-works-fuel-label = топливо
ui-city-works-amount = сколько единиц
ui-city-works-price = цена за единицу, ₭
ui-city-works-order-fuel = Заказать подвоз
ui-city-loan-title = Кредит казне
ui-city-loan-rule = Казна занимает у столицы на общественные работы: по ключевой, без маржи и без надбавок, на общей кредитной линии города — той же, что несёт займы граждан.
ui-city-loan-line = линия: занято { $occupied } ₭ из { $permitted } ₭
ui-city-loan-row = осталось { $outstanding } ₭ из { $principal } ₭ под { $rate }% · взят { $taken }
ui-city-loan-repay = Погасить из казны
ui-city-borrow = Занять у столицы ₭

## Эмиссия по подписям (D-270)

ui-emission-title = Эмиссия
ui-emission-rule = Столица печатает деньги в свою казну по подписям: заявку подаёт держатель права «эмиссия», печатают подписи { $share }% держателей. Считаются руки тех, кто держит право сейчас. Напечатанное входит в долю эмиссии — ставка увидит его сама.
ui-emission-holders = держателей права: { $holders } · нужно подписей: { $needed }
ui-emission-proposal = { $money } ₭ · предложил: { $who } · подписей { $signed } из { $needed } · срок: { $until }
ui-emission-sign = Подписать
ui-emission-signed = ваша подпись стоит
ui-emission-amount = сколько напечатать, ₭
ui-emission-print = Напечатать деньги

## Администрация: должности, права, законы, устав (D-154, D-155)

ui-admin-title = Администрация
ui-admin-no-city = Здесь нет города: за стенами законов нет.
ui-admin-title-city = Администрация · { $city }
ui-admin-tab-power = власть
ui-admin-tab-panel = панель
ui-admin-treasury-sign = казна { $treasury } ₭
ui-admin-upkeep = Городских узлов на содержании: { $nodes }. Они жгут { $energy } энергии за { $hours } ч — деньгами за них никто не платит, но по тарифу { $tariff } ₭ за 100 это { $worth } ₭ непроданной энергии.
ui-admin-resident = Вы здесь житель: законы видны, правят их должностные лица.
ui-admin-your-rights = Ваши права: { $rights }.
ui-admin-come-in = Решения принимаются в администрации — придите в неё.
ui-admin-offices = Должности
ui-admin-offices-none = должностей нет
ui-admin-revoke = Снять
ui-admin-create-office = Создать должность
ui-admin-whom = кого назначить
ui-admin-post-default = Министр экономики
ui-admin-post-title = название придумывает город, движок смотрит в права
ui-admin-appoint = Назначить
ui-admin-laws = Код-законы
ui-admin-law-own = решение города
ui-admin-law-default = умолчание
ui-admin-law-accept = Принять
ui-admin-laws-note = Право на закон точечное: «министр экономики» правит пошлины и не трогает налог. Отдать можно только то, что есть у себя.
ui-admin-lots = Свободные участки
ui-admin-which-lot = какой участок
ui-admin-to-whom = кому
ui-admin-allot = Выделить
ui-admin-treasury = Казна
ui-admin-pay = Заплатить ₭
ui-admin-charter = Устав
ui-admin-charter-rule = Устав решает, кто утверждает закон: «правитель единолично» меняет его сразу, «голосованием граждан» — созывает голосование. Выборы правителя и совет приедут своей механикой.

## Гражданство: одно на человека, вход по уставу, выход мгновенный (D-160, D-281)

ui-admin-admission-open = принимают свободно
ui-admin-admission-application = по заявке с одобрением
ui-admin-admission-invite = только по приглашению
ui-admin-citizenship = Гражданство
ui-admin-citizenship-in = состоите в
ui-admin-citizenship-none = Вы нигде не состоите: гость платит пошлины, но не налоги.
ui-admin-your-city = Это ваш город.
ui-admin-invited = Вас позвали: примите приглашение.
ui-admin-applied = Заявка подана — ждёт решения власти.
ui-admin-join-blocked = гражданство одно на человека: сначала выйти из прежнего города
ui-admin-accept-invite = Принять приглашение
ui-admin-join = Вступить в граждане
ui-admin-admission-line = { $city }: { $order }
ui-admin-leave-title = слово уходит по Сети: идти в ратушу незачем
ui-admin-leave = Выйти из гражданства
ui-admin-leave-note = Выход мгновенный. Держит одно: непогашенный кредит — с ним не выпустят, пока не рассчитаетесь.

## Слово городу (D-183)

ui-admin-word = Слово городу
ui-admin-word-none = город молчит: новичок видит одни числа
ui-admin-word-hint = чем город зовёт новичка
ui-admin-word-publish = Объявить
ui-admin-word-count = { $used } из { $limit } знаков · видно всем, кто выбирает, где напечататься

## Пошлина: товар, ставка и беспошлинная норма (D-123)

ui-admin-customs-open = граница открыта: ставок нет
ui-admin-customs-free = беспошлинно { $free } кг в сутки
ui-admin-customs-drop = Снять
ui-admin-customs-goods = товар
ui-admin-customs-rate-title = ставка, % от справочной цены
ui-admin-customs-free-title = беспошлинная норма, кг в сутки на человека
ui-admin-customs-add = Ввести

## Права должности

ui-admin-scopes-note = Права должности — отдать можно только своё:
ui-admin-scopes-lacking = нет у вас

## Экономическая панель (D-140)

ui-admin-panel-none = панель недоступна
ui-admin-panel-blind = Город слеп: администрация не стоит либо отключена за неуплату. Данные не обновляются, и решения принимаются вслепую.
ui-admin-panel-sign = за последние { $hours } ч · сделок { $trades } · оборот { $volume } ₭
ui-admin-panel-rule = Шаг сводки медленнее рынка нарочно: мгновенные данные дали бы власти торговое преимущество перед собственными купцами. Персонального здесь нет ни у кого — ни доходов, ни маршрутов.
ui-admin-panel-people = Люди
ui-admin-panel-people-line = в городе { $here } · напечаталось за период { $printed }
ui-admin-panel-energy = Энергия
ui-admin-panel-energy-line = в пуле { $stored } · тариф { $tariff } ₭ за 100 · на работу { $work } · на быт { $home }
ui-admin-panel-border = Граница
ui-admin-panel-border-line = ввезено { $imported } · вывезено { $exported }
ui-admin-panel-kg = { $goods } { $kg } кг
ui-admin-panel-trips = ходок: { $in } внутрь, { $out } наружу · пошлин собрано { $duty } ₭
ui-admin-panel-production = Производство
ui-admin-panel-production-line = добыто { $mined } · убрано { $harvested } · выпущено { $crafted }
ui-admin-panel-prices = Цены
ui-admin-panel-no-trades = сделок за период не было
ui-admin-panel-goods = Товар в городе
ui-admin-panel-treasury = Казна
ui-admin-panel-balance = остаток { $balance } ₭
ui-admin-panel-lent = роздано в кредит { $lent } ₭
ui-admin-panel-collected = собрано: { $lines }
ui-admin-panel-spent = потрачено: { $lines }
ui-admin-panel-ledger-line = { $ground } { $amount } ₭
ui-admin-panel-treasury-closed = Казна по статьям — тем, у кого есть право «панель города». Балансы, обороты и цены открыты всем: без этого спорить с властью нечем.

## Голосования (D-161, D-162)

ui-admin-threshold-simple = простое большинство
ui-admin-threshold-two-thirds = две трети
ui-admin-threshold-unanimous = единогласно
ui-admin-votes = Голосования
ui-admin-votes-rule = Голос подаётся по Сети — присутствие нужно, чтобы править, а не чтобы участвовать. Итог применится сам, когда выйдет срок.
ui-admin-call-election = Созвать выборы
ui-admin-call-council = Выборы в совет
ui-admin-call-recall = Отозвать правителя
ui-admin-votes-note = Итог применяется сам: избранный получает набор прежнего правителя, отзыв снимает должность и тут же созывает выборы.
ui-admin-vote-council = выборы в совет
ui-admin-vote-ruler = выборы правителя
ui-admin-vote-no-candidates = · кандидатов нет
ui-admin-vote-recall = отзыв правителя
ui-admin-vote-charter = устав
ui-admin-vote-charter-note = · порог задан самим уставом
ui-admin-vote-by-council = решает совет
ui-admin-vote-turnout = проголосовало { $yes } из { $of }
ui-admin-vote-tally = за { $yes } · против { $no } · из { $of }
ui-admin-vote-quorum = · кворум { $quorum }%
ui-admin-vote-closes = закроется { $when }
ui-admin-nominate = Выдвинуться
ui-admin-nominate-title = выдвинуться в правители
ui-admin-vote-for = За «{ $name }»
ui-admin-vote-yes = За
ui-admin-vote-no = Против
ui-admin-vote-none = голоса нет

## Суд (D-095, D-117, D-166, D-176)

ui-admin-court = Суд
ui-admin-case-open = ждёт суда
ui-admin-case-judged = приговор: { $sanction }
ui-admin-case-dismissed-why = отказано: { $why }
ui-admin-case-dismissed = отказано
ui-admin-sanction-unenforced = (не исполняется)
ui-admin-fine-title = сумма штрафа либо срок заключения в сутках
ui-admin-prison-title = в какую каторгу отправить
ui-admin-prison-pick = — каторга —
ui-admin-verdict = Приговор
ui-admin-dismiss = Отказать
ui-admin-sue-whom = на кого
ui-admin-sue-claim = суть жалобы
ui-admin-sue = Подать жалобу
ui-admin-sue-note = Жалоба стоит пошлины в казну города.
ui-admin-court-queue = Дел в очереди: { $count }. Судит тот, кому город дал право суда.
