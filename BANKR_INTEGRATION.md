# Bankr Integration in Clawmes

Ported from openclawnch — clawmes preserves the same Bankr feature surface
on a Hermes Agent backend.

## Uses Bankr — no alternative

These features require Bankr; there is no non-Bankr fallback path:

- Token launches on Base/Solana (Bankr sponsors gas, signs deploy tx)
- Leveraged trading via Avantis (1-10x long/short, Bankr-only)
- Polymarket prediction market execution on Polygon

## Uses Bankr — but alternatives exist

These default to Bankr when configured but fall back to a non-Bankr path
if Bankr is not connected:

- Custodial wallet (alternative: WalletConnect with your own wallet, or
  local encrypted wallet)
- Token swaps (alternative: 0x aggregator + WalletConnect signing)
- Server-side trading automations — limit buys, DCA, TWAP, stop-loss
  (alternative: local plan scheduler with cron, time/price/on-chain
  triggers — runs on your own instance, persists to disk, survives
  restarts)
- LLM inference gateway (alternative: bring your own Anthropic, OpenAI,
  OpenRouter, or Nous Portal key — Hermes' standard provider mechanism)
- LLM credit management / `/topup` / `/autotopup` (not applicable when
  using own API keys)

## Does not use Bankr at all

These features run entirely on user-controlled infrastructure:

- Aave V3 lending/borrowing
- Lido / Rocket Pool staking
- Uniswap V3/V4 liquidity provision
- Cross-chain bridging (Across, Stargate, LiFi)
- Yearn V3 vault strategies
- NFT management
- Gnosis Safe multisig
- Governance / DAO voting (Snapshot, Tally)
- Farcaster social
- Fiat on/off ramp (Bridge.xyz, MoonPay)
- Price feeds (DexScreener, CoinGecko, Chainlink, DefiLlama)
- Spending policies
- Local plan scheduling (cron, time/price/on-chain triggers,
  conditionals, parallel execution)
- Onboarding flow
- Agent memory, skill evolution, session recall
- Credential vault, prompt building, tool registration
