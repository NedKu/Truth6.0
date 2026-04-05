# Conversation Summary (Chronological)

> Workspace: [`truthasset.py`](truthasset.py:1) (Streamlit Truth 6.0 dashboard)

## 1) Initial product request: integrate Truth 6.0 rules into the Streamlit app

### User asked for
- Integrate Truth 6.0 rules into the app (implemented in [`truthasset.py`](truthasset.py:1)):
  - **Differentiated rebalancing bands**: stocks ±5%, gold ±2%, bonds ±5%.
  - **Maggiulli-style contribution-first rebalancing**: use new cash to fix underweights first; only sell if still out of band.
  - **FTD Guard**: after an FTD on `^GSPC`, if within 5 days price breaks the FTD-day low → force regime to 🟡 Caution and stop new funding.
  - **Level 5 ammo retention**: if VIX makes new highs and has positive slope → delay cash deployment.
  - **Bond protection**: if CPI > 3.5% and FEDFUNDS trending up → replace bond target with cash.
- Add explanations in the UI (expanders) and ensure `get_global_regime()` participates in final mode decision.

### Actions taken
- Added constants and rule explainer block near the top of [`truthasset.py`](truthasset.py:15).
- Implemented FTD detection + guard:
  - [`find_ftd_event()`](truthasset.py:177)
  - [`evaluate_ftd_guard()`](truthasset.py:204)
  - `FTD_INVALIDATION_WINDOW = 5` in [`truthasset.py`](truthasset.py:15)
- Implemented macro bond-protection switch `bond_protection_on` in the analysis section (based on CPI YoY and rate trend) in [`truthasset.py`](truthasset.py:548).
- Implemented VIX ammo delay logic in [`evaluate_vix_ammo_delay()`](truthasset.py:429).
- Implemented multi-market breadth regime via [`get_global_regime()`](truthasset.py:313), then combined it with the single-market regime and FTD guard to decide `decision_regime` in [`truthasset.py`](truthasset.py:589).

## 2) Explainability + “do not normalize” constraints

### User asked for
- The app must **NOT auto-normalize** allocations.
- Instead: **warn** and show **where drift comes from**.
- Clarified logic: **tilt depends only on `decision_regime` + age**; FTD only affects invest gating and can force Caution via guard (not direct tilt changes).

### Actions taken
- Added target-sum check for computed targets (error if not ~100) in Layer 2 in [`truthasset.py`](truthasset.py:722).
- Added real-holdings slider sum check and warning (no normalization) in the rebalancing simulator in [`truthasset.py`](truthasset.py:926).
- Added an explainability table in Layer 2 to make tilt and bond/cash split reasons explicit in [`truthasset.py`](truthasset.py:727).

## 3) Allocation-sum bug investigation and fix (targets summing to ~95%)

### User reported
- Allocation sums not equal to 100%.

### Root cause discovered
- `gold = 5` is fixed, but was being effectively **double-subtracted** in the defense bucket logic (the classic “95% pool + 5% gold” invariant was violated).
- The intended invariant is:
  - `stocks + bonds + cash = 95`
  - `gold = 5`
  - Total = `100`

### Fix applied in code
- In [`calc_truth_alloc()`](truthasset.py:374), the defense bucket is computed as:
  - `defense_after = 95 - stk_f`
  - allocate cash first: `csh_f = min(b_csh, defense_after)`
  - bonds are residual: `bnd_f = defense_after - csh_f`
  - bond protection moves bonds into cash when enabled.

Key snippet (current state):
```py
# in [`calc_truth_alloc()`](truthasset.py:374)
stk_f = max(0.0, min(95.0, b_stk + tilt))
defense_after = max(0.0, 95.0 - stk_f)
csh_f = min(b_csh, defense_after)
bnd_f = max(0.0, defense_after - csh_f)

if bond_protection_on:
    csh_f += bnd_f
    bnd_f = 0.0
```

### Plan artifact created
- A written plan capturing the above root cause + invariants + explainability approach was created as:
  - [`plans/tilt-allocation-sum-fix-plan.md`](plans/tilt-allocation-sum-fix-plan.md:1)

## 4) UI/UX restructuring requests (Master mode)

### User asked for
- Move the “blood-red drawdown monitor panel (5-stage drawdown + 200MA marker)” under Layer 1 market-state table.
- Wrap it in an expander.
- Integrate Master-mode multi-asset radar into Layer 1 market-state instead of a separate redundant table.
- FTD column should be human-readable text.
- The drawdown panel expander should be **open by default**.

### Actions taken
- Layer 1 market-state table now includes drawdown %, trend, and FTD status text:
  - Layer 1 data build in [`truthasset.py`](truthasset.py:642).
  - FTD display text example: `FTD Confirmed ✅` / `未確認 ⏳` in [`truthasset.py`](truthasset.py:582).
- “🩸 血色抄底監控面板” moved and wrapped as:
  - [`with st.expander(..., expanded=True):`](truthasset.py:671)

## 5) FTD definition consistency discussion → unify logic across tickers

### User asked
- Why FTD conditions were inconsistent and how to do it better.
- User selected unification approach: unify the FTD definition across tickers and gating.

### Actions taken
- `check_ftd_confirmed` was changed to rely on the same FTD logic used elsewhere:
  - [`check_ftd_confirmed()`](truthasset.py:271)
  - It now requires volume and uses [`find_ftd_event()`](truthasset.py:177).

Current snippet:
```py
# in [`check_ftd_confirmed()`](truthasset.py:271)
def check_ftd_confirmed(close_s, vol_s=None):
    if vol_s is None:
        return False
    ftd_event = find_ftd_event(close_s, vol_s)
    return bool(ftd_event.get("is_ftd", False))
```

- Updated call sites to pass volume:
  - `ftd_confirmed = check_ftd_confirmed(df_close["VT"], df_vol["VT"])` in [`truthasset.py`](truthasset.py:522)
  - Per-ticker loop: `asset_ftd = check_ftd_confirmed(series, df_vol[ticker])` in [`truthasset.py`](truthasset.py:573)

## 6) Errors encountered during the work

### NameError (constant order)
- A `NameError` occurred earlier when a constant (example: `STOCK_REBALANCE_BAND`) was referenced before definition due to an explainer/expander block being placed above constants.
- Resolution: move the expander below constants so the f-string references have defined names.

### Tool-enforcement error
- A system/tooling enforcement error occurred: “You did not use a tool in your previous response”.
- Resolution: subsequent steps used tool calls for follow-ups and edits.

## 7) Latest explicit user direction (pending work)

### User asked (latest)
- Remove the “new funding / contribution” part to simplify and clarify the rebalancing feature:
  - “新入金的部分直接移除，優化再平衡feature的清晰度”

### What is still present in code (needs removal)
- Constant: `MONTHLY_CONTRIBUTION_PCT = 5.0` in [`truthasset.py`](truthasset.py:19)
- Rebalancing function name + semantics: [`contribution_first_rebalance()`](truthasset.py:443)
- UI copy and action mapping that explicitly references new contributions:
  - Rule overview expander text in [`truthasset.py`](truthasset.py:26)
  - Final decision flow text mentions contribution-first in [`truthasset.py`](truthasset.py:835)
  - Rebalancing section text and trigger labels:
    - “BUY_WITH_CONTRIBUTION”, “SELL_ONLY_IF_CONTRIBUTION_FAILS” mapping in [`truthasset.py`](truthasset.py:950)
    - Rebalance summary message referencing contribution-first in [`truthasset.py`](truthasset.py:1006)

### Expected next implementation steps (to be done in Code mode)
- Replace contribution-first rebalancing with a simpler band-based rebalancing:
  - Remove `MONTHLY_CONTRIBUTION_PCT` usage.
  - Replace trigger vocabulary with BUY / SELL / HOLD (and keep CASH tactical/vix-delay logic if desired).
  - Update UI explainer text blocks to remove contribution/new-funding references.
  - Update reason strings accordingly.

---

## Appendix: Current rebalancing bands and where they live
- Constants in [`truthasset.py`](truthasset.py:16):
  - `STOCK_REBALANCE_BAND = 5.0`
  - `GOLD_REBALANCE_BAND = 2.0`
  - `BOND_REBALANCE_BAND = 5.0`

## Appendix: Current rebalancing function (still contribution-first)
- [`contribution_first_rebalance()`](truthasset.py:443)
  - Returns triggers like `BUY_WITH_CONTRIBUTION`, `SELL_ONLY_IF_CONTRIBUTION_FAILS`, `SELL_TO_TARGET`, plus cash states.
