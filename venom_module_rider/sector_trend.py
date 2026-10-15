"""Image occupancy trend only; never a distance, TTC or motion permission."""


def update(sectors, previous):
    history = {}
    result = {}
    for side, row in sectors.items():
        values = list((previous or {}).get(side, []))
        if row.get("state") == "unknown" or "outlier_fraction" not in row:
            values = []
        else:
            values = (values + [row["outlier_fraction"]])[-3:]
        trend = "insufficient"
        if len(values) == 3:
            changes = [values[i + 1] - values[i] for i in range(2)]
            if all(delta > 0.002 for delta in changes):
                trend = "growing"
            elif all(delta < -0.002 for delta in changes):
                trend = "receding"
            else:
                trend = "stable"
        history[side] = values
        result[side] = {**row, "occupancy_trend": trend}
    return result, history
