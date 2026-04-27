---
name: lending
description: Supply, borrow, repay, and withdraw on Aave V3 and Compound
metadata:
  hermes:
    tags: [crypto, evm, defi, lending, aave]
    category: clawmes
    requires_tools: [defi_lend, defi_balance]
---

# Lending & Borrowing

Use the `defi_lend` tool to supply collateral, borrow against it, repay, and withdraw — primarily on Aave V3 (the deepest market on every chain we support).

## When to use

- "Supply X to Aave"
- "Borrow Y against my deposited collateral"
- "Repay my loan"
- "What's my health factor?"

## Actions

| Action | What | Required args |
|---|---|---|
| `supply` | Deposit asset as collateral | `asset`, `amount` |
| `borrow` | Open a borrow position against collateral | `asset`, `amount`, `mode` (variable/stable) |
| `repay` | Pay down a borrow | `asset`, `amount` |
| `withdraw` | Pull collateral out (must keep health factor > 1.0) | `asset`, `amount` |
| `health_factor` | Read current HF + LTV | `address` (optional, defaults to connected wallet) |
| `info` | Aave market state for an asset | `asset` |

## Common flows

### Supply USDC for yield

1. Check current USDC supply APY via `defi_lend action="info" asset="USDC"`.
2. Surface APY + utilization rate. Aave rates are dynamic.
3. Confirm amount with the user.
4. Call `defi_lend action="supply"`. Receipt returns `aTokens` minted (the receipt token representing your deposit).

### Borrow against collateral

1. Confirm there's collateral. Call `defi_lend action="health_factor"` to see current state.
2. Borrow rule of thumb: keep HF > 1.5 to comfortably ride out volatility. HF < 1.0 = liquidation.
3. Show the user: target borrow → projected HF after borrow.
4. Variable rate is the default. Stable rate (when available) is locked in but typically more expensive — only worth it if you're holding a borrow long-term.
5. Call `defi_lend action="borrow"`.

### Repay a borrow

1. Aave repayments accept the underlying asset OR the aToken receipt.
2. Use `defi_lend action="repay"` with the borrowed asset and amount.
3. To repay in full, pass `amount="max"`; we resolve to the actual debt at submission time (debt accrues interest by the second).

## Pitfalls

- **Health factor**: HF < 1.0 triggers liquidation — usually 5-10% of the position is taken with a fee. Always show HF on borrow.
- **Borrow against borrowed**: most chains permit recursive borrowing for leverage. Useful for delta-neutral strategies but risky — surface clearly when user is doing this.
- **Stable vs variable rate**: stable rates can be re-balanced by the protocol if utilization changes too fast. Read carefully.
- **eMode**: Aave V3 has efficiency-mode for correlated assets (e.g. ETH-correlated). Higher LTV but limited to that asset cluster. Only enable when user explicitly asks.

## Verification

- Post-supply: `defi_balance` will show the aToken (e.g. `aBasUSDC` for USDC on Base) — that's your receipt.
- Post-borrow: `defi_balance` shows the borrowed asset in the wallet.
- Health factor: `defi_lend action="health_factor"` returns the current value with the threshold context.

## Related

- `defi_balance` — pre/post position checks
- `defi_swap` — convert between assets before/after lending
- `bridge` — move collateral to a different chain's Aave pool
- `cost_basis` — track interest accrued
