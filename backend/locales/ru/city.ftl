# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Город и всё, что решается в нём: участки, казна, должности, устав,
# гражданство, голосования, суд, двери локаций, Сеть и разговор
# (D-089, D-155, D-160, D-163, D-166, D-204, D-222).
#
# Две несовместимые привычки Fluent, из-за которых файл выглядит так:
#   — перенос в ТЕКСТЕ значения сохраняется в отказе, поэтому текст пишется
#     одной строкой, какой бы длинной она ни вышла;
#   — варианты выбора ({ $x -> ... }) обязаны стоять каждый на своей строке,
#     и эти переносы в текст не попадают.
#
# Имена узлов, городов и людей — уже слова, они едут простым { $arg }.
# NAME($id) нужен только устойчивым ключам вольта.

# --- участки города (D-089) ---------------------------------------------------

city-land-not-civic = это не городской участок
city-land-not-a-plot = «{ $node }» — не участок под раздачу, а собственная локация города: город раздаёт участки, а не себя
city-land-taken = участок уже за кем-то
city-land-dead = мёртвое тело участками не распоряжается
city-land-cede-on-foot = участок передают ногами: дойдите до него
city-land-not-yours = участок не ваш: городу отдают своё
city-land-not-city-land = это не городская земля: здесь некому её передать
city-land-city-missing = участок приписан к несуществующему городу
city-land-deed-on-sale = бумага на участок выставлена на продажу: снимите её с торгов, иначе покупатель заплатит за чужое
city-land-debt = на узле долг { $debt } ₭: сначала закройте счёт, город чужих долгов не принимает

# --- казна (D-155) ------------------------------------------------------------

city-treasury-zero = трата на ноль — это не трата
city-treasury-short = в казне { $have } ₭, а нужно { $need } ₭

# --- основание города (D-023, D-098, D-159) -----------------------------------

city-found-dead = мёртвое тело городов не основывает
city-body-off-node = тело вне узла
city-found-planet-only = город закладывают на узле планеты: в чужой застройке города не заводят
city-found-foreign-land = это чужой участок: город на нём не закладывают
city-found-already-city = здесь уже стоит город
city-found-already-civic = это уже городская земля
city-found-not-ready = для города не хватает: { $missing }. Порог входа — постройки, а не монета

# Роли, без которых города не бывает (D-023, D-098, D-159). Ключи, а не слова:
# их называет и отказ, и окно основания, и обоим нужен язык читателя.
city-role-bioprinter = биопринтер
city-role-administration = администрация
city-role-market = рынок
city-role-power = источник энергии
city-found-no-name = у города должно быть имя
city-found-name-too-long = имя города длиннее { $limit } знаков: его несут и карточка, и летопись, и официальный канал
city-founder-exists = у города «{ $city }» уже есть основатель

# --- власть и должности (D-155, D-164, D-231) ---------------------------------

city-no-power = нет права «{ $power }» в городе «{ $city }»: власть — это должность, а не намерение
city-hall-dead = управлять городом можно только живым телом
city-hall-not-territory = это не территория города «{ $city }»: власть осуществляется у себя
city-hall-absent = здесь нет администрации: решения города принимаются в ней
city-hall-cut-off = администрация отключена за неуплату: город без неё слеп и нем
city-hall-frozen = «{ $node }» промёрз: администрация закрыта, пока узел не обогрет
city-powers-not-own = нельзя передать то, чего нет у себя: { $extra }
city-office-no-powers = должность без полномочий — это не должность
city-office-other-city = должность не этого города
city-founder-not-dismissed = основателя снимает устав, а не приказ: см. `ruler_recall` и `charter.silence_days`

# --- законы и устав (D-163) ---------------------------------------------------

city-no-such-law = нет такого код-закона: { $law }
city-no-such-question = нет такого вопроса устава: { $question }
city-no-such-option = нет такого варианта: { $option }
city-option-requires = вариант «{ $option }» требует ответа на «{ $requires }»
city-charter-sealed = устав этого города не меняется: так решил он сам
city-about-too-long = слово города длиннее { $limit } знаков: карточку читают за десять секунд

# --- гражданство (D-160, D-184) -----------------------------------------------

city-already-citizen-here = вы уже гражданин этого города
city-citizenship-is-one = гражданство одно на человека: сначала выйти из прежнего города
city-by-invitation-only = в этот город принимают только по приглашению: ждите зова власти
city-already-citizen = { $who } уже гражданин
city-no-application = заявки от этого человека нет
city-already-in-a-city = { $who } уже состоит в городе
city-not-a-citizen-anywhere = вы нигде не состоите
city-bound-by-printing = гражданство взято условием печати и держит до { $until } UTC. Этот срок вы приняли, выбрав дверь города
city-not-a-citizen-here = { $who } не гражданин этого города

# --- голосования и совет (D-163, D-164) ---------------------------------------

vote-is-an-election = это выборы: здесь голосуют за человека, а не «за» или «против»
vote-is-a-poll = это не выборы: здесь голосуют «за» или «против»
vote-closed = голосование закрыто: опоздавший голос итога не меняет
vote-election-closed = выборы закрыты
vote-no-voice-in-poll = голоса нет: в этом голосовании решают { $voters ->
        [council] члены совета
       *[citizens] граждане
    }
vote-no-voice-in-election = голоса нет: в этих выборах решают { $voters ->
        [council] члены совета
       *[citizens] граждане
    }
vote-nominee-needs-voice = выдвигается тот, у кого есть голос в этих выборах: { $voters ->
        [council] члены совета
       *[citizens] граждане
    }
vote-ruler-not-elected = устав города не отдал власть выборам: правитель определяется иначе
vote-no-recall = устав города не допускает отзыва правителя
vote-no-ruler-to-recall = отзывать некого: правителя нет
vote-not-an-election = это не выборы: выдвигаться некуда
vote-nominate-while-open = выдвигаются, пока идут выборы
vote-not-nominated = { $who } не выдвигался
vote-no-council = устав этого города не заводит совета
vote-council-full = в совете { $seats } мест, и все заняты: сначала освободить место
vote-council-not-appointed = места этого совета не назначают: устав отдал их выборам
vote-council-needs-voice = в совет садятся граждане, отвечающие цензу устава
vote-council-not-elected = устав этого города не выбирает совет

# --- суд (D-117, D-166) -------------------------------------------------------

justice-empty-claim = жалоба без сути — не жалоба
justice-self-claim = на себя не жалуются
justice-too-late = с события прошло больше { $days } суток: срок давности вышел
justice-cannot-pay-fee = пошлина суда { $fee } ₭, а на счету меньше
justice-case-judged = дело уже рассмотрено
justice-case-nowhere = дело ссылается в никуда
justice-not-a-judge = судит тот, кому город дал право justice
justice-no-such-sanction = нет такой санкции: { $sanction }
justice-unenforceable = «{ $sanction }» движок пока не исполняет: приговор без исполнения — хуже, чем отказ от приговора
justice-defendant-gone = ответчик исчез
justice-not-a-prison = «{ $node }» — не каторга этого города
justice-many-prisons = в городе несколько каторг: суд называет, в какую отправить

# --- земля и застройка (D-089, D-192, D-198, D-220, D-247) --------------------

estate-unknown-kind = «{ KIND($kind) }» — не тип здания; строят из: { KINDS($kinds) }
estate-build-dead = мёртвое тело не строит
estate-build-on-foot = строят ногами: дойдите до участка
estate-build-not-on-storey = это этаж, а не участок: дом строят на земле — спуститесь во двор
estate-build-house-stands = на участке уже стоит дом или заложена площадка: второй рядом не закладывают
estate-build-not-yours = участок не ваш: строят у себя
estate-build-no-floors = дом без этажей — это яма
estate-build-not-on-pyroxis = на Пироксисе не строят: землетрясения рушат постройки быстрее, чем их ставят. Жильё здесь — борт корабля
estate-build-too-small = пятно меньше { NUMBER($smallest, maximumFractionDigits: 0) } м² — это навес, а не здание: просят { NUMBER($area, maximumFractionDigits: 0) }
estate-build-no-room = на участке { NUMBER($plot, maximumFractionDigits: 0) } м², свободно { NUMBER($free, maximumFractionDigits: 0) }{ $started ->
        [true] , в стройке { NUMBER($going, maximumFractionDigits: 0) }
       *[false] {""}
    }: ещё { NUMBER($area, maximumFractionDigits: 0) } не помещается
estate-build-already-queued = стройка уже поставлена
estate-build-job-nowhere = стройка { $job } ссылается в никуда
# Стройплощадка (D-266): фазы и их отказы. Подстановка имени — только
# меткой, кавычками или деталью за тире (D-258).
estate-site-nowhere = площадка { $site } ссылается в никуда
estate-site-not-here = площадка на другом участке: дойдите до неё
estate-site-not-gathering = площадка уже не собирает: стройка идёт или закончена
estate-site-not-needed = «{ NAME($goods) }» в смете нет
estate-site-material-full = { NAME($goods) }: внесено сполна
estate-site-nothing-to-add = вносить нечего: меньше одной штуки
estate-site-not-yours = площадка не ваша: начинает и завершает хозяин
estate-site-short = внесено не всё: не хватает { NUMBER($short, maximumFractionDigits: 1) } — { NAME($goods) }
estate-site-no-strength = сил не хватает: стройка возьмёт { NUMBER($need, maximumFractionDigits: 1) } выносливости, есть { NUMBER($have, maximumFractionDigits: 1) }
estate-site-already-started = стройка уже начата
estate-site-not-ready = дом ещё не готов: стройка идёт

estate-deed-not-a-plot = «{ $node }» — собственная локация города, а не участок: такой бумагой не торгуют
estate-deed-not-yours = бумага не ваша: продают своё
estate-deed-not-on-sale = бумага не выставлена на продажу
estate-deed-own = своя бумага не покупается
estate-deed-addressed = договор адресный: бумага обещана другому
estate-deed-site-open = на участке стройплощадка: землю с ней не продают, пока дом не встал
estate-deed-too-dear = бумага стоит { $price } ₭, а на счету { $have } ₭

estate-demolish-dead = мёртвое тело не сносит
estate-demolish-on-foot = сносят ногами: дойдите до участка
estate-demolish-not-yours = участок не ваш: сносят своё, а чужую городскую застройку разбирают по решению суда, а не кнопкой
estate-demolish-nothing = сносить нечего: здания на участке нет
estate-demolish-blocked = { $why }

# Что мешает сносу (D-197). Ключи, а не фразы: этот список читают и отказ,
# и окно, гасящее кнопку, — оба на языке того, кто смотрит.
estate-blocker-equipment = в здании стоит оборудование ({ $count }): рабочие станции и мебель забирают до сноса — после него им негде стоять
estate-blocker-overloaded = на полу { NUMBER($floor, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг и во дворе { NUMBER($yard, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг, а участок держит { NUMBER($holds, minimumFractionDigits: 1, maximumFractionDigits: 1) } кг: лишнее увезите или уложите в сундук
estate-blocker-building = здесь идёт стройка: сначала дождитесь её конца
estate-blocker-demolishing = снос уже идёт: второй раз его не заказывают
estate-demolish-already-queued = снос уже поставлен
estate-demolish-job-nowhere = снос { $job } ссылается в никуда

estate-land-no-price = город не назначил цену земли: код-закон `land_price` пуст
estate-land-buy-dead = мёртвое тело не покупает
estate-land-buy-on-foot = участок покупают ногами: дойдите до него
estate-land-taken = участок уже за кем-то
estate-land-not-civic = это не городская земля: за городом её не продают и не присваивают, но работать и строить там может всякий
estate-land-not-a-plot = «{ $node }» — не участок под продажу, а собственная локация города: город продаёт участки, а не себя
estate-land-not-vacant = узел не пустой: застройку и жилы города прейскурант не продаёт
estate-land-city-missing = узел приписан к несуществующему городу
estate-land-permit = «{ $city }» продаёт землю не всякому: код-закон build_permit — «{ $permit }». Вступите в граждане
estate-land-too-dear = участок стоит { $price } ₭, а на счету { $have } ₭

estate-about-dead = мёртвое тело ничего не описывает
estate-about-on-foot = до участка надо дойти: описание пишут на месте
estate-about-not-yours = участок не ваш: описание даёт хозяин, а городской земле — власть с правом на участки
estate-about-too-long = описание длиннее { $limit } знаков

estate-emblem-dead = мёртвое тело значков не прибивает
estate-emblem-on-foot = до участка надо дойти: значок прибивают на месте
estate-emblem-not-yours = участок не ваш: значок ставит хозяин, а городской земле — власть с правом на участки
estate-emblem-unknown = такого значка нет: выбирают из предложенных

estate-rename-dead = мёртвое тело ничего не переименовывает
estate-rename-on-foot = до участка надо дойти: табличку прибивают на месте
estate-rename-not-yours = участок не ваш: имя даёт хозяин, а городской земле — власть с правом на участки
estate-rename-no-name = у участка должно быть имя
estate-rename-too-long = имя длиннее { $limit } знаков

estate-repair-dead = мёртвое тело не чинит
estate-repair-on-foot = чинят руками: дойдите до участка
estate-repair-not-yours = участок не ваш: чинят у себя
estate-repair-under-way = ремонт уже идёт: второй раз его не заказывают
estate-repair-nothing = чинить нечего: на участке нет здания
estate-repair-intact = дом целёхонек: чинить в нём нечего
estate-repair-already-queued = ремонт уже поставлен
estate-repair-job-nowhere = ремонт { $job } ссылается в никуда

# --- дверь локации (D-198, D-204, D-247) --------------------------------------

access-door-downstairs = дверь у места, а не у этажа в нём: вход закрывают внизу, на участке
access-no-holder = { $land ->
        [city] это городская земля: вход на неё решают гражданством и пошлиной, а не дверью локации
       *[wild] у этой земли нет хозяина: за городом дверей не ставят
    }
access-not-yours = локация не ваша: дверью распоряжается хозяин
access-self-in-list = себя в списках не держат: хозяин входит всегда
access-barred = «{ $node }» — чужая локация, и хозяин вас туда не пускает. Пройти через неё можно, остановиться — нет; пустить вас может только хозяин

# --- Сеть (D-222) -------------------------------------------------------------

net-no-body = без тела в Сети только читают: писать нечем
net-empty = { $what ->
        [letter] письмо пусто
        [name] название пусто
       *[post] пост пуст
    }
net-too-long = { $what ->
        [letter] письмо
        [name] название
       *[post] пост
    } длиннее { $limit } знаков
net-letter-to-self = письмо себе — это дневник, а не Сеть
net-not-your-thread = это не ваша переписка
net-about-too-long = описание длиннее { $limit } знаков
net-channel-exists = канал «{ $channel }» уже есть
net-no-such-channel = нет такого канала
net-own-channel-kept = от своего канала не отписываются
net-city-channel-kept = канал своего города читают всегда: это гражданство, а не подписка
net-cannot-post = { $channel ->
        [own] в этот канал пишет его автор
       *[city] в канал города пишут с правом «channel»
    }

# --- разговор (D-043) ---------------------------------------------------------

chat-dead-are-silent = мёртвые не разговаривают
chat-nothing-to-say = сказать нечего
chat-too-long = реплика длиннее { $limit } знаков
chat-group-not-here = этот кружок не здесь

# Единственная реплика, которую в комнату говорит сам сервер: передача вещи
# из рук в руки видна всем, кто здесь стоит. Реплика — одна запись на всю
# комнату, поэтому пишется на языке мира по умолчанию, как и хроника.
chat-hands-over = передаёт { $named ->
        [true] { $who }
       *[false] —
    }: { NAME($goods) }{ $counted ->
        [true] { " " }×{ $amount }
       *[false] {""}
    }

## Эмиссия по подписям (D-270)

emission-not-capital = деньги печатает только столица, а «{ $city }» — не она
emission-not-positive = сумма эмиссии должна быть больше нуля
emission-proposal-open = заявка ещё собирает подписи: { $money } ₭; вторая рядом с ней не стоит
emission-no-proposal = такой заявки на эмиссию нет
emission-proposal-closed = заявка уже закрыта: деньги напечатаны или срок вышел
emission-proposal-expired = срок заявки вышел, подписи под ней больше не собирают
emission-already-signed = ваша подпись под этой заявкой уже стоит
cmd-no-such-proposal = нет такой заявки
