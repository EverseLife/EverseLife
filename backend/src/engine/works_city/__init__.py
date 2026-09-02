# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""City orders on the works board, and the treasury as a borrower (D-248, wave 3).

Where road orders are physics -- the engine sees a sagged edge itself -- what
to repair, what to build and which station to supply is **politics**: the city
decides, holding the TREASURY power, standing in its own administration. The
state does not choose what the city needs; it subsidises the labour on what
the city chose.

## Who pays what

The fund buys no goods (D-002). Every non-labour cost -- materials the worker
walls in, fuel they pour -- is the **city's offer**, a sum the city names when
posting. The labour tariff is the engine's public formula, split by
`works.city_cofinance`: the city fronts its share, the fund adds the rest.
Both parts are escrowed at posting -- an order the money is not set aside for
does not go up.

City orders are claimless like road orders: taking one is doing the work, and
the engine pays whoever it verified. Repair and construction on the city's own
plot are licensed by the open order itself -- the order **is** the permission,
revocable by withdrawing the order. Fuel is poured through the ordinary
station mechanic and paid per unit as it lands.

Split out of `engine/works.py` before it crossed the size bar, the way
`seed.py` was cut (D-243).

## The treasury as a borrower

The city may borrow from the CB for its works: at the key rate, with no
margin and no risk premium -- a city cannot mark itself up -- on its own
credit line, which since D-285 is measured by what the city has earned the
right to owe rather than by a flat share of turnover. The same line and the
same primitive as the borrowing a citizen's loan sets off when the treasury
is short (D-283): one road to the capital, not two.

Nor is a treasury left to its conscience any more: while an overdue loan of
the city stands, the capital keeps a share of everything that comes into the
treasury until it is settled (D-285).
"""

from src.engine.works_city._base import (  # noqa: F401
    WorksCityError,
    labor_tariff,
    licensed,
    open_city_order,
    split_labor,
)
from src.engine.works_city.credit import (  # noqa: F401
    borrow_for_works,
    repay_for_works,
    treasury_loans,
)
from src.engine.works_city.order import (  # noqa: F401
    cancel_city_order,
    post_build_order,
    post_fuel_order,
    post_repair_order,
)
from src.engine.works_city.pay import (  # noqa: F401
    pay_build_order,
    pay_fuel_delivery,
    pay_repair_order,
)
