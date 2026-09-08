"""Shipped, text-only vocabulary of The Tower's card names.

GAME knowledge only, like player/catalogue.py is for modules: the fixed set of
names the game prints on cards, the same for every account. It exists for one
job - repair a confident OCR slip into the spelling the game actually shows
('Free LJpgrades' -> 'Free Upgrades', 'Stow Aura' -> 'Slow Aura') so the same
physical card is not recorded under two names.

It never decides WHICH cards an account owns, how many there are, or when a
scan ends - those come only from the tiles on screen (card_inventory dedupes by
absolute inventory position, not by name). A reading that is not a close and
UNAMBIGUOUS match to a known name is returned untouched: new cards the table
lacks pass straight through, and a genuinely ambiguous blob stays uncertain
rather than being forced onto the nearest label.

No pixels: this carries names, never art. Card artwork stays the account's own
local screen crop (CLAUDE.md's image-free release boundary).
"""
import difflib

# The names the game prints on cards. Not closed: an unknown reading passes
# through untouched, so a missing entry costs a spelling repair, never a card.
CARD_NAMES = (
    "Damage",
    "Attack Speed",
    "Critical Chance",
    "Critical Coin",
    "Range",
    "Health",
    "Health Regen",
    "Extra Defense",
    "Fortress",
    "Cash",
    "Coins",
    "Slow Aura",
    "Enemy Balance",
    "Free Upgrades",
    "Extra Orb",
    "Plasma Cannon",
    "Wave Skip",
    "Intro Sprint",
    "Land Mine Stun",
    "Recovery Package Chance",
    "Cells",
    "Death Ray",
    "Energy Net",
    "Super Tower",
    "Second Wind",
    "Demon Mode",
    "Energy Shield",
    "Wave Accelerator",
    "Berserker",
    "Ultimate Crit",
    "Nuke",
)

_BY_LOWER = {name.lower(): name for name in CARD_NAMES}


def resolve(text, *, high=0.84, floor=0.62, margin=0.06, strong_margin=0.12):
    """OCR'd card label -> the game's spelling, or None to keep the raw reading.

    An exact (case-insensitive) hit returns immediately. A fuzzy hit is accepted
    when it is either highly similar (>= `high`) or only moderately similar
    (>= `floor`) but UNAMBIGUOUS - clearly closer to one name than to any other
    (a `strong_margin` lead over the runner-up). That repairs a badly degraded
    but unmistakable reading ('Ceus' -> 'Cells') while a low-similarity or
    contested reading ('Cish' between Cash and Coins, or a genuinely new card)
    scores too low or too close to win, and is kept verbatim / left uncertain.
    """
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in _BY_LOWER:
        return _BY_LOWER[low]
    scored = sorted(
        (difflib.SequenceMatcher(None, low, key).ratio(), key)
        for key in _BY_LOWER
    )
    best, best_key = scored[-1]
    gap = best - (scored[-2][0] if len(scored) > 1 else 0.0)
    if (best >= high and gap >= margin) or (best >= floor and gap >= strong_margin):
        return _BY_LOWER[best_key]
    return None
