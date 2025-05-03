# Smart Order Router Backtest (Cont & Kukanov Model)

## Overview
This project implements a Smart Order Router based on the static cost model from *Cont & Kukanov (2012)*, "Optimal Order Placement in Limit Order Markets." The router optimally splits a 5,000-share buy order across multiple venues to minimize execution cost, penalizing underfills, overfills, and queue risk via three parameters:
- `lambda_over`: penalty per share for overfilling,
- `lambda_under`: penalty per share for underfilling,
- `theta_queue`: penalty for queue position uncertainty.

The backtest replays real L1 market data and benchmarks the optimized strategy against three baselines:
- **Best Ask**: always hit the lowest displayed ask,
- **TWAP**: equal-sized slices across 60-second buckets,
- **VWAP**: volume-weighted price across all venues.

---

## Approach

1.  **Data Loading & Snapshotting:**
    *   Loads tick data (timestamp, venue, ask price, ask size).
    *   Cleans and sorts the data.
    *   Groups messages by timestamp into market snapshots, representing the state of all venues at discrete times. Duplicates per venue within a snapshot are removed.

2.  **Smart Order Router (SOR) Simulation (`run_router`):**
    *   Iterates through the time-ordered snapshots.
    *   At each snapshot, if the order is not yet filled, it determines an allocation strategy using the `allocate` function for the *remaining* order size.
    *   **Allocation (`allocate`):** Performs a brute-force combinatorial search (with discrete `STEP` size) across available venues to find the allocation that minimizes a cost function.
    *   **Cost Function (`compute_cost`):** Evaluates a potential allocation based on:
        *   Execution cost (price + `FEE`) for the filled portion.
        *   Maker `REBATE` for the portion of the allocation *not* immediately filled.
        *   Penalties for underfill (`l_under`) and overfill (`l_over`) relative to the target size.
        *   A general deviation penalty (`theta`).
    *   Executes the determined `split` (allocation) against the snapshot's liquidity, respecting available size per venue and the remaining order quantity.
    *   Tracks the cumulative cash spent and quantity filled over time.

3.  **Parameter Tuning (`tune_router`):**
    *   Performs a grid search over predefined ranges for the penalty parameters (`l_list` for over/underfill, `theta_list` for deviation).
    *   Runs the full SOR simulation for each parameter combination.
    *   Selects the parameter set that results in the **lowest total cash spent** while ensuring the **entire order is filled**.

4.  **Baseline Strategies:**
    *   **Best Ask (`best_ask`):** A simple greedy strategy that always buys from the venue offering the lowest ask price at each snapshot until the order is filled.
    *   **TWAP (`twap`):** Resamples the market data into 60-second buckets. Attempts to execute an equal fraction of the total order in each bucket, limited by available liquidity, at the bucket's best price.
    *   **VWAP (`vwap`):** Calculates the volume-weighted average price across the *entire* input dataset.

5.  **Evaluation & Output:**
    *   Calculates the total cost and average execution price for the optimized SOR and all baseline strategies.
    *   Computes the savings in Basis Points (BPS) achieved by the SOR relative to each baseline.
    *   Generates a plot (`results.png`) showing the cumulative execution cost over time for all strategies.
    *   Saves the best parameters, final costs, average prices, and BPS savings to `results.json`.

*Note: The given dataset only seemed to have one unique venue, but the code logic implemented should work well for any number of venues.*

---

##  Parameter Ranges
The router searches over a small grid of risk parameters, closer to the optimal values obtained by Cont & Kukanov:
- `lambda_over` ∈ {0, 0.025, 0.05}
- `lambda_under` ∈ {0, 0.025, 0.05}
- `theta_queue` ∈ {0, 0.00025, 0.0005}

The search is exhaustive and only accepts fully-filled allocations. The best parameter set is selected based on the lowest total cost.

*Note: The analysis of the provided `results.json` showed that the optimal parameters were all zero. This suggests that for this dataset and configuration, explicitly penalizing potential under/overfill or queuing risk *within the single-step cost evaluation* did not lead to a better *overall* simulation outcome. The greedy optimization of immediate execution cost (price + fee - rebate) at each step, combined with the simulation proceeding through time, was sufficient.*

---

## Output
Upon completion, the script produces:
- A `results.json` (also printed to `stdout`) file summarizing:
  - Best parameters
  - Router vs. baseline costs and average prices
  - Savings in basis points (BPS) vs. each baseline
- A `results.png` plot showing cumulative cost over time for all strategies

---

## Possible Improvement: Adverse Selection-Aware Cost Model

To better reflect market dynamics, I propose extending the static cost model with an **adverse selection risk term**, inspired by the empirical observation in Cont & Kukanov that large trades may *move* the market, causing poorer subsequent fills.

### Rationale
Section 3 of the paper notes that optimal placement balances between market orders (immediate but costly) and limit orders (cheaper but uncertain). However, the model assumes *posted quotes remain unchanged* after routing. In real markets, aggressive routing at a venue may lead to:

- Quote revisions,
- Liquidity withdrawal,
- Or quote shading by counterparties anticipating informed flow.

### Implementation Sketch
We introduce a new parameter `gamma_adv` and define:

$$
AdjustedCost_{i} = Price_{i} + Fee_{i} + \gamma_{adv} \times \frac{OrderSize_{i}}{DisplayedSize_{i}}
$$

This penalizes allocations that consume a high fraction of visible liquidity, anticipating negative price movements. It encourages diversification across venues and discourages over-reliance on shallow books.

This approach is aligned with the paper’s goal of modeling execution risk, while extending it toward **impact-aware routing**, thereby making the allocator more robust in adversarial or informed-flow settings.

---

## Running
```bash
python3 backtest.py
````

Ensure `l1_day.csv` is present in the working directory.

---

## Files

* `backtest.py` – full pipeline: data load, optimization, simulation, plotting
* `results.json` – summary metrics and savings
* `results.png` – cost vs. time comparison
* `README.md` – this documentation

---