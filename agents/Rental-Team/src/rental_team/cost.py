from decimal import Decimal


def cost(listing, months):
    unknown = []
    rent = listing.rent if listing.rent_unit == "CNY/month" else None
    if rent is None:
        unknown.append("monthly_rent")
    if listing.monthly_fees is None:
        unknown.append("fixed_monthly_fees")
    if listing.one_time_fees is None:
        unknown.append("nonrefundable_one_time_fees")
    known_monthly = sum((v for v in (rent, listing.monthly_fees) if v is not None), Decimal(0))
    has_monthly = rent is not None or listing.monthly_fees is not None
    known_cost = (
        known_monthly * months + (listing.one_time_fees or Decimal(0))
        if has_monthly or listing.one_time_fees is not None else None
    )
    first_payment = None
    if (rent is not None and listing.first_rent_months is not None
            and listing.deposit is not None and listing.monthly_fees is not None
            and listing.one_time_fees is not None):
        first_payment = (
            (rent + listing.monthly_fees) * listing.first_rent_months
            + listing.deposit + listing.one_time_fees
        )
    return {
        "currency": "CNY", "months": months,
        "known_monthly": str(known_monthly) if has_monthly else None,
        "known_lease_cost": str(known_cost) if known_cost is not None else None,
        "coverage": "complete_fixed_costs" if not unknown else "known_partial_cost",
        "refundable_deposit": str(listing.deposit) if listing.deposit is not None else None,
        "first_payment": str(first_payment) if first_payment is not None else None,
        "unknowns": unknown,
        "usage_costs": "水电等按实际使用结算；未估算",
    }


def conditions(listing, requirements):
    monthly = listing.rent
    if requirements.budget_kind == "known_monthly":
        monthly = (
            listing.rent + listing.monthly_fees
            if listing.rent is not None and listing.monthly_fees is not None else None
        )
    result = {
        "city": None if not listing.city else
        listing.city.removesuffix("市") == requirements.city.removesuffix("市"),
        "rent_type": None if not listing.rent_type else listing.rent_type == requirements.rent_type,
        "budget": None if monthly is None or listing.rent_unit != "CNY/month" else
        requirements.rent_min <= monthly <= requirements.rent_max,
        "move_in": None,
    }
    if requirements.location:
        result["location"] = None
    if requirements.bedrooms:
        result["bedrooms"] = None
    for condition in requirements.hard_conditions:
        result[condition] = None
    if requirements.max_commute_minutes:
        result["commute"] = (
            listing.commute["minutes"] <= requirements.max_commute_minutes
            if listing.commute.get("status") == "queried" else None
        )
    return result


def compare(listings, requirements):
    verified, incomplete, excluded = [], [], []
    for listing in listings:
        checks = conditions(listing, requirements)
        row = {
            "listing": listing.model_dump(mode="json"),
            "conditions": checks, "cost": cost(listing, requirements.months),
            "possible_duplicates": [{
                "source": other.source, "house_id": other.house_id,
                "reason": "位置、户型与租金相同，仅关联待核验，不认定为同一套房",
            } for other in listings
                if other.source != listing.source
                and listing.location and other.location == listing.location
                and listing.layout and other.layout == listing.layout
                and listing.rent is not None and listing.rent == other.rent],
            "risks": [
                {"kind": "needs_inspection", "text": "核对采光、噪音、漏水、设备与房源实际可租状态"},
                {"kind": "general_contract", "text": "核对产权或出租授权、退租扣费与押金返还条款"},
            ],
        }
        if False in checks.values():
            excluded.append(row)
        elif None in checks.values() or listing.conflicts:
            incomplete.append(row)
        else:
            verified.append(row)
    return {
        "candidates": verified, "needs_verification": incomplete, "excluded": excluded,
        "checklist": [
            "核对原平台房源ID、更新时间、位置和实际可入住日期",
            "现场核验房屋与出租授权，不仅依赖平台描述",
            "书面核实押付、服务费、水电计价、维修及退租扣款",
            "按实际出行时段试走通勤路线",
        ],
        "decision": "无自动交易；未知费用不按零计算，不生成无依据总分",
    }
