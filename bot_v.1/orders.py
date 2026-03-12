"""Order analysis helpers for active and preview demand accounting."""
from __future__ import annotations

from collections import Counter

from .models import BotInfo, GameState, OrderInfo, OrderStatus

COMMIT_MODE_OPTIMISTIC = "optimistic"
COMMIT_MODE_COMMITTED = "committed"
COMMIT_MODE_DELIVERED_ONLY = "delivered_only"


def _remaining_after_delivered(order: OrderInfo) -> list[str]:
    remaining = list(order.items_required)
    for delivered in order.items_delivered:
        if delivered in remaining:
            remaining.remove(delivered)
    return remaining


def _normalized_commit_mode(commitment_mode: str | None) -> str:
    mode = str(commitment_mode or COMMIT_MODE_OPTIMISTIC).strip().lower()
    if mode in {
        COMMIT_MODE_OPTIMISTIC,
        COMMIT_MODE_COMMITTED,
        COMMIT_MODE_DELIVERED_ONLY,
    }:
        return mode
    return COMMIT_MODE_OPTIMISTIC


def infer_active_committed_bot_ids(
    state: GameState,
    *,
    commit_radius: int = 2,
) -> set[int]:
    """Infer bots likely to deliver active cargo soon.

    Heuristic: a bot with active-matching cargo is considered committed if it is
    close to drop-off or inventory-full (delivery pressure).
    """
    active = get_active_order(state)
    if active is None:
        return set()

    remaining = list(_remaining_after_delivered(active))
    if not remaining:
        return set()

    drop_off = (int(state.drop_off[0]), int(state.drop_off[1]))
    radius = max(0, int(commit_radius))
    committed: set[int] = set()

    for bot in sorted(state.bots, key=lambda row: int(row.id)):
        matching_now = 0
        for item_type in bot.inventory:
            if item_type in remaining:
                matching_now += 1
        if matching_now <= 0:
            continue

        dist_to_drop = abs(int(bot.pos.x) - drop_off[0]) + abs(int(bot.pos.y) - drop_off[1])
        near_drop = dist_to_drop <= radius
        inv_full = len(bot.inventory) >= 3
        if not (near_drop or inv_full):
            continue

        committed.add(int(bot.id))
        for item_type in bot.inventory:
            if item_type in remaining:
                remaining.remove(item_type)
        if not remaining:
            break

    return committed


def compute_active_delivered_deficit(state: GameState) -> list[str]:
    """Active order deficit reduced only by delivered items."""
    active = get_active_order(state)
    if active is None:
        return []
    return _remaining_after_delivered(active)


def compute_active_serviceable_deficit(
    state: GameState,
    *,
    committed_bot_ids: set[int] | None = None,
    commit_radius: int = 2,
) -> list[str]:
    """Active order deficit reduced by delivered + committed in-transit items."""
    active = get_active_order(state)
    if active is None:
        return []

    needed = _remaining_after_delivered(active)
    if not needed:
        return []

    committed = (
        {int(bot_id) for bot_id in committed_bot_ids}
        if committed_bot_ids is not None
        else infer_active_committed_bot_ids(state, commit_radius=commit_radius)
    )
    if not committed:
        return needed

    for bot in sorted(state.bots, key=lambda row: int(row.id)):
        if int(bot.id) not in committed:
            continue
        for item_type in bot.inventory:
            if item_type in needed:
                needed.remove(item_type)
        if not needed:
            break
    return needed


def compute_needed_items(
    state: GameState,
    *,
    commitment_mode: str = COMMIT_MODE_OPTIMISTIC,
    committed_bot_ids: set[int] | None = None,
    commit_radius: int = 2,
) -> list[str]:
    """Return active-order needed item types under the selected commitment mode."""
    mode = _normalized_commit_mode(commitment_mode)
    if mode == COMMIT_MODE_DELIVERED_ONLY:
        return compute_active_delivered_deficit(state)
    if mode == COMMIT_MODE_COMMITTED:
        return compute_active_serviceable_deficit(
            state,
            committed_bot_ids=committed_bot_ids,
            commit_radius=commit_radius,
        )

    # Optimistic legacy mode: delivered + all held inventory treated as in transit.
    active = get_active_order(state)
    if active is None:
        return []

    needed = _remaining_after_delivered(active)
    for bot in state.bots:
        for item_type in bot.inventory:
            if item_type in needed:
                needed.remove(item_type)
    return needed


def compute_preview_items(
    state: GameState,
    *,
    commitment_mode: str = COMMIT_MODE_OPTIMISTIC,
    committed_bot_ids: set[int] | None = None,
    commit_radius: int = 2,
) -> list[str]:
    """Return preview-order deficit under the selected commitment mode."""
    del committed_bot_ids  # Reserved for future call sites that pass explicit commitment sets.
    del commit_radius

    preview = get_preview_order(state)
    if preview is None:
        return []

    needed = _remaining_after_delivered(preview)
    mode = _normalized_commit_mode(commitment_mode)
    if mode == COMMIT_MODE_DELIVERED_ONLY:
        return needed

    if mode == COMMIT_MODE_OPTIMISTIC:
        # Legacy: all inventory not consumed by active order is considered preview carryover.
        active_consumed: list[str] = []
        active = get_active_order(state)
        if active is not None:
            still_needed = _remaining_after_delivered(active)
            for bot in state.bots:
                for item_type in bot.inventory:
                    if item_type in still_needed:
                        active_consumed.append(item_type)
                        still_needed.remove(item_type)

        for bot in state.bots:
            remaining_inv = list(bot.inventory)
            for active_type in active_consumed:
                if active_type in remaining_inv:
                    remaining_inv.remove(active_type)
            for item_type in remaining_inv:
                if item_type in needed:
                    needed.remove(item_type)
        return needed

    # Committed mode: reserve held inventory for active deficit first.
    reserved_for_active = Counter(compute_active_delivered_deficit(state))
    for bot in sorted(state.bots, key=lambda row: int(row.id)):
        for item_type in bot.inventory:
            if reserved_for_active.get(item_type, 0) > 0:
                reserved_for_active[item_type] -= 1
                continue
            if item_type in needed:
                needed.remove(item_type)
    return needed


def should_prefetch_preview(
    state: GameState,
    *,
    commitment_mode: str = COMMIT_MODE_OPTIMISTIC,
    committed_bot_ids: set[int] | None = None,
    commit_radius: int = 2,
    preview_safety_slots: int = 0,
) -> bool:
    """Allow preview picks when active deficit still fits into free capacity."""
    mode = _normalized_commit_mode(commitment_mode)
    remaining_active_items = len(
        compute_needed_items(
            state,
            commitment_mode=mode,
            committed_bot_ids=committed_bot_ids,
            commit_radius=commit_radius,
        )
    )
    remaining_inventory_slots = sum(max(0, 3 - len(bot.inventory)) for bot in state.bots)
    if remaining_inventory_slots <= 0:
        return False
    if mode == COMMIT_MODE_OPTIMISTIC:
        return remaining_active_items <= remaining_inventory_slots

    safety_slots = max(0, int(preview_safety_slots))
    return remaining_active_items + safety_slots <= remaining_inventory_slots


def get_active_order(state: GameState) -> OrderInfo | None:
    for order in state.orders:
        if order.status == OrderStatus.ACTIVE:
            return order
    return None


def get_preview_order(state: GameState) -> OrderInfo | None:
    for order in state.orders:
        if order.status == OrderStatus.PREVIEW:
            return order
    return None


def items_matching_active(bot: BotInfo, state: GameState) -> list[str]:
    """Return inventory items matching active delivered deficit."""
    active = get_active_order(state)
    if active is None:
        return []
    still_needed = _remaining_after_delivered(active)
    matching: list[str] = []
    for item_type in bot.inventory:
        if item_type in still_needed:
            matching.append(item_type)
            still_needed.remove(item_type)
    return matching
