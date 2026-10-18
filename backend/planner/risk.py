"""Weather/load risk rules and route ranking. Pure functions, no I/O."""

LOW, MODERATE, HIGH, SEVERE, NO_TRAVEL = range(5)
LEVEL_NAMES = ["Low", "Moderate", "High", "Severe", "No Travel"]

# Lower bound of Moderate, High, Severe, No Travel for each condition.
WIND_MPH = (25, 35, 45, 55)  # No Travel is ">= 55"
RAIN_IN_HR = (0.10, 0.25, 0.50, 1.00)  # No Travel is "> 1.00"
SNOW_IN_HR = (0.5, 1.0, 2.0, 3.0)  # No Travel is "> 3.0"


def _level(value, bounds, no_travel_inclusive):
    """Band a value. Shared edges (e.g. rain 0.25) go to the higher band,
    except the No Travel edge, which the table makes '>=' for wind and '>' for rain/snow."""
    moderate, high, severe, no_travel = bounds
    if value > no_travel or (no_travel_inclusive and value == no_travel):
        return NO_TRAVEL
    if value >= severe:
        return SEVERE
    if value >= high:
        return HIGH
    if value >= moderate:
        return MODERATE
    return LOW


def classify(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    """Risk level of one checkpoint plus human-readable reasons for it."""
    wind = _level(wind_mph, WIND_MPH, True)
    rain = _level(rain_in_hr, RAIN_IN_HR, False)
    snow = _level(snow_in_hr, SNOW_IN_HR, False)
    level = max(wind, rain, snow)
    reasons = []
    if wind == level:
        reasons.append(f"Wind {wind_mph:.0f} mph")
    if rain == level and rain > LOW:
        reasons.append(f"Rain {rain_in_hr:.2f} in/hr")
    if snow == level and snow > LOW:
        reasons.append(f"Snow {snow_in_hr:.1f} in/hr")

    # Load rules (>= 55 mph is already No Travel for any load).
    load_rule = None
    if wind == SEVERE and load_lb > 30_000:
        load_rule = (NO_TRAVEL, "30,000")
    elif wind == HIGH and load_lb > 40_000:
        load_rule = (SEVERE, "40,000")
    if load_rule and load_rule[0] >= level:
        if load_rule[0] > level:
            reasons = []
        level = load_rule[0]
        reasons.append(f"Wind {wind_mph:.0f} mph + load > {load_rule[1]} lb")
    if level == LOW:
        reasons = ["Calm conditions"]
    return level, reasons


def _index(value, bounds):
    """Continuous 0..4 version of the bands, for a smooth heatmap."""
    lo = 0.0
    for i, hi in enumerate(bounds):
        if value < hi:
            return i + (value - lo) / (hi - lo)
        lo = hi
    return 4.0


def heat_indices(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    """(overall, wind, rain, snow) on the 0..4 scale; overall includes the load rules."""
    w, r, s = _index(wind_mph, WIND_MPH), _index(rain_in_hr, RAIN_IN_HR), _index(snow_in_hr, SNOW_IN_HR)
    overall = max(w, r, s)
    if wind_mph >= 45 and load_lb > 30_000:
        overall = 4.0
    elif wind_mph >= 35 and load_lb > 40_000:
        overall = max(overall, 3.0)
    return overall, w, r, s


def summarize(checkpoints):
    """Miles at each level and distance-weighted average risk for one route.
    Each checkpoint stands for the road halfway to its neighbours."""
    miles = [0.0] * 5
    for a, b in zip(checkpoints, checkpoints[1:]):
        half = (b["mile"] - a["mile"]) / 2
        miles[a["level"]] += half
        miles[b["level"]] += half
    total = sum(miles)
    return {
        "miles_by_level": {name: round(m, 1) for name, m in zip(LEVEL_NAMES, miles)},
        "avg_risk": round(sum(i * m for i, m in enumerate(miles)) / total, 2) if total else 0.0,
        "max_level": max(c["level"] for c in checkpoints),
    }


def rank_key(route):
    """Preference order from the spec: fewest Severe miles, fewest High miles,
    lowest average risk, shortest time. No Travel miles count before Severe ones."""
    m = route["summary"]["miles_by_level"]
    return (m["No Travel"], m["Severe"], m["High"], route["summary"]["avg_risk"], route["duration_h"])


CRITERIA = ["Fewest No Travel miles", "Fewest Severe miles", "Fewest High miles", "Lowest average risk", "Shortest travel time"]


def recommend(routes):
    """Index of the best route and a one-line reason it beat the runner-up."""
    order = sorted(range(len(routes)), key=lambda i: rank_key(routes[i]))
    best = order[0]
    if len(order) == 1:
        return best, "Only route available."
    a, b = rank_key(routes[best]), rank_key(routes[order[1]])
    for name, x, y in zip(CRITERIA, a, b):
        if x != y:
            unit = " h" if name == "Shortest travel time" else ""
            return best, f"{name}: {x}{unit} vs {y}{unit} on {routes[order[1]]['name']}."
    return best, "Routes are equivalent; picked the first."
