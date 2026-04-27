---
name: staking
description: Stake ETH via Lido or Rocket Pool; native staking on supported chains
metadata:
  hermes:
    tags: [crypto, evm, defi, staking, lido, rocketpool]
    category: clawmes
    requires_tools: [defi_stake, defi_balance, defi_price]
---

# Staking

Use the `defi_stake` tool to stake ETH (or native tokens on supported chains) via the dominant LSTs: Lido (`stETH`) and Rocket Pool (`rETH`). Both produce liquid receipt tokens you can use elsewhere in DeFi while accruing staking yield.

## When to use

- "Stake my ETH"
- "Get stETH" / "Get rETH"
- "What's the current staking APY?"
- "Unstake"

## Actions

| Action | What | Required args |
|---|---|---|
| `stake` | Stake into a chosen protocol | `protocol` (lido/rocketpool), `amount` |
| `unstake` | Begin the unstaking flow (LSTs use a queue) | `protocol`, `amount` |
| `claim` | Claim available rewards / withdrawals | `protocol` |
| `info` | APY + protocol-specific stats | `protocol` |

## Common flows

### Stake ETH for stETH (Lido)

1. Check `defi_stake action="info" protocol="lido"` for current APY (usually 3-4% on Lido).
2. Confirm the amount. Lido has no minimum.
3. Call `defi_stake action="stake" protocol="lido" amount="1.5"`. You receive 1:1 stETH minus a small fee.
4. stETH is rebasing — your wallet balance ticks up daily, no claim needed.

### Stake ETH for rETH (Rocket Pool)

1. Check info; APY is typically similar to Lido but slightly higher due to commission structure.
2. RPL has a minimum stake (currently 0.01 ETH).
3. Call `defi_stake action="stake" protocol="rocketpool" amount="1.0"`.
4. rETH appreciates in value vs ETH (rather than rebasing). Your token count stays constant; the redemption rate climbs.

### Unstake

- Lido: `unstake` enters the withdrawal queue (queue length varies; check info). When ready, `claim` releases the ETH.
- Rocket Pool: similar queue with rETH burn → ETH unlock.
- Faster path for both: just swap the LST back to ETH on a DEX (`defi_swap`). Liquid markets keep the LST close to peg.

## Pitfalls

- **Validator slashing risk**: small but non-zero. Spread across protocols if staking large amounts.
- **Depeg risk**: stETH/rETH can trade below 1.0 ETH in stress events (May 2022, March 2023). Liquid markets generally arbitrage back, but withdrawal queue can be days/weeks.
- **Tax implications**: rebasing (Lido) is treated as ordinary income in many jurisdictions; appreciation (Rocket Pool) is capital gain at sale. Not financial advice — consult a tax professional.
- **Validator centralization**: Lido is the largest single LST. For long-term stakes, diversifying across protocols also reduces protocol-concentration risk.

## Verification

- Post-stake: `defi_balance action="token"` for the LST contract (stETH / rETH) confirms receipt.
- APY drift: stETH balance will increase visibly day over day; rETH price (vs ETH) will tick up over time.

## Related

- `defi_swap` — fast unstake via DEX
- `defi_lend` — use stETH/rETH as collateral on Aave
- `yield` — find the best protocol APY across the LST landscape
- `bridge` — move LSTs between chains (most are wrapped on L2s)
