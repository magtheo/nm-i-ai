# Failure Modes

- loaded bots abandon locally useful inventory and increase future delivery latency
- secondary repositioning adds motion without improving score
- extra movement increases collisions or corridor contention
- proxy reduction in `idle_fallback` does not translate into more completed orders
- candidate changes behavior only in synthetic edge cases and has negligible effect in real Expert runs
