"""Order analysis — compute needed items, track in-transit items."""
from __future__ import annotations

from .models import GameState, OrderInfo, OrderStatus, BotInfo


def compute_needed_items(state: GameState) -> list[str]:
    """Return list of item *types* still needed for the active order.

    Accounts for:
    - items already delivered
    - items currently in any bot's inventory (assumed in-transit)
    """
    active = get_active_order(state)
    if active is None:
        return []

    needed = list(active.items_required)

    # Remove already-delivered items
    for d in active.items_delivered:
        if d in needed:
            needed.remove(d)

    # Remove items already held by bots (in-transit to drop-off)
    for bot in state.bots:
        for item_type in bot.inventory:
            if item_type in needed:
                needed.remove(item_type)

    return needed


def compute_preview_items(state: GameState) -> list[str]:
    """Return item types still needed for the preview order.

    Subtracts inventory items that are NOT consumed by the active order,
    since those will auto-deliver to the preview order on completion.
    """
    preview = get_preview_order(state)
    if preview is None:
        return []

    needed = list(preview.items_required)

    # Determine which inventory items are consumed by the active order
    active_consumed: list[str] = []
    active = get_active_order(state)
    if active is not None:
        still_needed = list(active.items_required)
        for d in active.items_delivered:
            if d in still_needed:
                still_needed.remove(d)
        for bot in state.bots:
            for item_type in bot.inventory:
                if item_type in still_needed:
                    active_consumed.append(item_type)
                    still_needed.remove(item_type)

    # Remaining inventory after active consumption → these auto-deliver
    for bot in state.bots:
        remaining_inv = list(bot.inventory)
        for ac in active_consumed:
            if ac in remaining_inv:
                remaining_inv.remove(ac)
        # Subtract auto-deliverable items from preview needs
        for item_type in remaining_inv:
            if item_type in needed:
                needed.remove(item_type)

    return needed


def should_prefetch_preview(state: GameState) -> bool:
    """Allow preview picks only when active needs can still fit in free slots."""
    remaining_active_items = len(compute_needed_items(state))
    remaining_inventory_slots = sum(max(0, 3 - len(bot.inventory)) for bot in state.bots)
    if remaining_inventory_slots <= 0:
        return False
    return remaining_active_items <= remaining_inventory_slots


def get_active_order(state: GameState) -> OrderInfo | None:
    for o in state.orders:
        if o.status == OrderStatus.ACTIVE:
            return o
    return None


def get_preview_order(state: GameState) -> OrderInfo | None:
    for o in state.orders:
        if o.status == OrderStatus.PREVIEW:
            return o
    return None


def items_matching_active(bot: BotInfo, state: GameState) -> list[str]:
    """Return items in bot's inventory that match the active order's remaining needs."""
    active = get_active_order(state)
    if active is None:
        return []
    still_needed = list(active.items_required)
    for d in active.items_delivered:
        if d in still_needed:
            still_needed.remove(d)
    matching = []
    for item_type in bot.inventory:
        if item_type in still_needed:
            matching.append(item_type)
            still_needed.remove(item_type)
    return matching
