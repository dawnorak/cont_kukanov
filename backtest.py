import pandas as pd
import matplotlib.pyplot as plt
import json
from itertools import product
import sys

# Global constants for order execution
ORDER_SIZE = 5000
STEP = 100  # allocation granularity

# fee per executed share, and rebate for unexecuted portion (values adapted from Cont-Kukanov paper)
FEE = 0.003
REBATE = 0.002

# Build a list of venue dictionaries for snapshot: ['venue', 'ask', 'ask_size', 'fee', 'rebate']
def build_snapshot(df):
    return [{
        'venue': row['publisher_id'],
        'ask': row['ask_px_00'],
        'ask_size': row['ask_sz_00'],
        'fee': FEE,
        'rebate': REBATE
    } for _, row in df.iterrows()]

# Compute the total cost of allocation
def compute_cost(split, venues, order_size, l_over, l_under, theta):
    executed = cash_spent = 0.0
    for i in range(len(venues)):
        fill = min(split[i], venues[i]['ask_size'])
        executed += fill
        cash_spent += fill * (venues[i]['ask'] + venues[i]['fee'])
        # Rebate is applied to unfilled portion
        cash_spent -= max(split[i] - fill, 0) * venues[i]['rebate']
    # Penalties for underfill and overfill
    underfill = max(order_size - executed, 0)
    overfill = max(executed - order_size, 0)
    return cash_spent + theta * (underfill + overfill) + l_under * underfill + l_over * overfill

# Brute-force allocation search using fixed step
def allocate(order_size, venues, l_over, l_under, theta):
    splits = [[]]
    for v_idx, venue in enumerate(venues):
        new_splits = []
        max_qty = int(venue['ask_size'])
        for alloc in splits:
            used = sum(alloc)
            remaining = order_size - used
            # All possible allocations to this venue up to step granularity
            for q in range(0, min(remaining, max_qty) + 1, STEP):
                new_splits.append(alloc + [q])
        splits = new_splits or [s + [0] * (len(venues) - v_idx) for s in splits]
        if not splits: break

    # Find split with min total cost
    best_cost, best_split = float('inf'), []
    for alloc in splits:
        if sum(alloc) == order_size:
            cost = compute_cost(alloc, venues, order_size, l_over, l_under, theta)
            if cost < best_cost:
                best_cost, best_split = cost, alloc
    return best_split + [0] * (len(venues) - len(best_split)), best_cost

# Simulate the router over snapshot stream
def run_router(snapshots, l_over, l_under, theta):
    remaining, cash_spent, cum_qty = ORDER_SIZE, 0.0, 0
    cum_data = [(snapshots[0][0], 0, 0.0)]  # (timestamp, quantity filled, total cost)

    for ts, venues in snapshots:
        if remaining <= 0 or not venues:
            break
        split, _ = allocate(remaining, venues, l_over, l_under, theta)
        cash_step = qty_step = 0
        for i, qty in enumerate(split):
            fill = min(qty, venues[i]['ask_size'], remaining)
            cash_step += fill * venues[i]['ask']
            qty_step += fill
            remaining -= fill
        if qty_step > 0:
            cash_spent += cash_step
            cum_qty += qty_step
            cum_data.append((ts, cum_qty, cash_spent))

    avg_price = cash_spent / cum_qty if cum_qty else 0
    return cash_spent, avg_price, cum_data

# Search for best router params
def tune_router(snapshots, l_list, theta_list):
    best = {"cost": float('inf'), "params": {}, "avg": None, "data": []}
    for l_over, l_under, theta in product(l_list, l_list, theta_list):
        cash, avg, data = run_router(snapshots, l_over, l_under, theta)
        filled = data[-1][1] if data else 0
        # Only accept fully filled routes
        if abs(filled - ORDER_SIZE) < 1e-6 and cash < best["cost"]:
            best.update({
                "cost": cash,
                "avg": avg,
                "params": {"lambda_over": l_over, "lambda_under": l_under, "theta_queue": theta},
                "data": data
            })
    return best

# Naive Best Ask
def best_ask(snapshots):
    remaining, cash, cum_qty = ORDER_SIZE, 0.0, 0
    cum_data = [(snapshots[0][0], 0, 0.0)]
    for ts, venues in snapshots:
        if remaining <= 0 or not venues:
            continue
        best = min(venues, key=lambda v: v['ask'])
        qty = min(remaining, best['ask_size'])
        cash += qty * best['ask']
        remaining -= qty
        cum_qty += qty
        cum_data.append((ts, cum_qty, cash))
    avg_price = cash / cum_qty if cum_qty else 0
    return cash, avg_price, cum_data

# Time-weighted Average Price strategy (60s buckets)
def twap(df, bucket_sec=60):
    df = df.set_index('ts_event')
    # Resample data into 60-second buckets
    binned = df.groupby(pd.Grouper(freq=f"{bucket_sec}s")).agg(
        ask_px=('ask_px_00', 'min'),
        ask_sz=('ask_sz_00', 'sum')
    ).dropna()

    if binned.empty:
        return 0.0, 0.0, [(df.index.min(), 0, 0.0)]

    per_bucket = ORDER_SIZE / len(binned)
    remaining, cash, cum_qty = ORDER_SIZE, 0.0, 0
    cum_data = [(binned.index.min(), 0, 0.0)]

    for ts, row in binned.iterrows():
        qty = min(per_bucket, remaining, row.ask_sz)
        cash += qty * row.ask_px
        remaining -= qty
        cum_qty += qty
        cum_data.append((ts, cum_qty, cash))
        if remaining <= 0: break

    avg_price = cash / cum_qty if cum_qty else 0
    return cash, avg_price, cum_data

# Volume-weighted Average Price over entire period
def vwap(df):
    total_sz = df['ask_sz_00'].sum()
    vwap = (df['ask_px_00'] * df['ask_sz_00']).sum() / total_sz if total_sz else 0
    return vwap * ORDER_SIZE, vwap

# Plot cost over time for all strategies
def plot_cost(router_data, best_ask_data, twap_data, vwap_price, filename="results.png"):
    plt.figure(figsize=(15, 8))
    if router_data:
        plt.plot([x[0] for x in router_data], [x[2] for x in router_data], label='Router', linewidth=2)
    if best_ask_data:
        plt.plot([x[0] for x in best_ask_data], [x[2] for x in best_ask_data], label='Best Ask', linestyle='--')
    if twap_data:
      df_twap = pd.DataFrame(twap_data, columns=['ts_event', 'cum_qty', 'cum_cost'])
      plt.step(df_twap['ts_event'], df_twap['cum_cost'], where='post', label='TWAP', linestyle=':')
    if router_data and vwap_price > 0:
        vwap_line = [(ts, qty * vwap_price) for ts, qty, _ in router_data]
        plt.plot([x[0] for x in vwap_line], [x[1] for x in vwap_line], label=f'VWAP', linestyle='-.')
    plt.xlabel("Time")
    plt.ylabel("Cumulative Cost")
    plt.title(f"Execution Cost Comparison")
    plt.legend()
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(filename)

# Main script logic
if __name__ == "__main__":
    # Load and preprocess the message-level market data
    df = pd.read_csv("l1_day.csv", usecols=["ts_event", "publisher_id", "ask_px_00", "ask_sz_00"], parse_dates=["ts_event"])
    df = df[df['ask_sz_00'] > 0].dropna().sort_values(by=['ts_event', 'publisher_id'])

    # Group messages into snapshots and extract venues
    snapshots = [(ts, build_snapshot(g.drop_duplicates('publisher_id'))) for ts, g in df.groupby('ts_event') if not g.empty]

    if not snapshots:
        print("No valid snapshots found.")
        sys.exit(1)

    # Search parameter ranges
    l_vals = [0, 0.025, 0.05]
    theta_vals = [0, 0.00025, 0.0005]

    # Tune the smart order router
    best = tune_router(snapshots, l_vals, theta_vals)

    # Run baseline strategies
    best_ask_cash, best_ask_avg, best_ask_data = best_ask(snapshots)
    twap_cash, twap_avg, twap_data = twap(df.copy())
    vwap_cash, vwap_avg = vwap(df)

    def bps(diff, base):
        return 1e4 * diff / base if base else 0

    # Output JSON
    results = {
        "best_params": best["params"],
        "router": {"cash_spent": best["cost"], "avg_price": best["avg"]},
        "baselines": {
            "best_ask": {"cash_spent": best_ask_cash, "avg_price": best_ask_avg},
            "twap": {"cash_spent": twap_cash, "avg_price": twap_avg},
            "vwap": {"cash_spent": vwap_cash, "avg_price": vwap_avg}
        },
        "savings_bps": {
            "vs_best_ask": bps(best_ask_cash - best["cost"], best_ask_cash),
            "vs_twap": bps(twap_cash - best["cost"], twap_cash),
            "vs_vwap": bps(vwap_cash - best["cost"], vwap_cash)
        }
    }

    # Save plot and JSON output
    plot_cost(best["data"], best_ask_data, twap_data, vwap_avg)

    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(json.dumps(results, indent=2))