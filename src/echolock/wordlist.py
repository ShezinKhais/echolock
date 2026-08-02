"""Word pool for the daily passphrase.

Words are chosen to be easy to say and easy for a small offline speech model to
transcribe: common English nouns, no proper nouns, and (enforced by a test) no
two entries within the edit distance the liveness matcher tolerates. That
last property matters for correctness, not tidiness: if "kitten" and "mitten"
both sat in the pool, a recording of one would satisfy a prompt for the other,
and two different prompts accepting the same audio weakens exactly the guarantee
the daily phrase exists to provide.

Phrase secrecy is not the goal, since the phrase is displayed on screen when it
is needed, so this list is deliberately public and readable.
"""

from __future__ import annotations

WORDS: tuple[str, ...] = (
    "anchor", "apple", "arrow", "autumn", "bacon", "badge", "bamboo",
    "banjo", "basket", "bicycle", "biscuit", "blanket", "blossom", "bottle",
    "boulder", "branch", "bridge", "bucket", "bundle", "cabin", "cactus",
    "camera", "candle", "canvas", "canyon", "captain", "cargo", "carpet",
    "castle", "cedar", "cellar", "chalk", "cherry", "chimney", "cinnamon",
    "circus", "clover", "cobalt", "compass", "copper", "coral", "cottage",
    "cotton", "crater", "crayon", "crimson", "crystal", "cyclone", "dagger",
    "daisy", "dolphin", "domino", "donkey", "dragon", "drifter", "dungeon",
    "eagle", "ember", "engine", "envelope", "fabric", "falcon", "feather",
    "fiddle", "figure", "filter", "flannel", "flint", "forest", "fossil",
    "fountain", "fragment", "frontier", "gadget", "gallon", "garden",
    "garnet", "gecko", "geyser", "ginger", "glacier", "glimmer", "granite",
    "gravel", "guitar", "hammer", "hamster", "harbor", "harvest", "hazel",
    "helmet", "hexagon", "hollow", "honey", "hurdle", "iceberg", "igloo",
    "impala", "indigo", "island", "ivory", "jacket", "jaguar", "jasmine",
    "jelly", "jigsaw", "jungle", "junior", "kettle", "kingdom", "kitten",
    "koala", "ladder", "lagoon", "lantern", "lattice", "lemon", "leopard",
    "lighthouse", "lilac", "linen", "lobster", "locket", "lumber", "magnet",
    "mammoth", "mango", "maple", "marble", "marigold", "meadow", "melon",
    "meteor", "mineral", "mirror", "monsoon", "mosaic", "muffin", "mustard",
    "nectar", "needle", "nickel", "nomad", "noodle", "notebook", "nugget",
    "nutmeg", "oatmeal", "obsidian", "octagon", "octopus", "olive", "onyx",
    "opal", "orbit", "orchid", "otter", "outpost", "oxygen", "oyster",
    "paddle", "palace", "pancake", "panther", "paprika", "parcel",
    "parsley", "pasture", "peacock", "pebble", "pelican", "pencil",
    "penguin", "pepper", "petal", "pewter", "pickle", "pigment", "pillow",
    "pineapple", "pistol", "planet", "plateau", "platinum", "plaza",
    "polish", "pollen", "poplar", "portal", "pottery", "prairie", "pretzel",
    "prism", "pudding", "pumpkin", "puzzle", "pyramid", "quarry", "quartz",
    "quilt", "rabbit", "radish", "rafter", "rainbow", "ranger", "rattle",
    "raven", "ribbon", "ripple", "river", "rodeo", "rooster", "rosemary",
    "rubble", "ruby", "saffron", "sailor", "salmon", "sandal", "sapphire",
    "satchel", "scarlet", "scooter", "seagull", "sequoia", "shadow",
    "shamrock", "shelter", "sherbet", "shingle", "shovel", "shuttle",
    "silver", "siren", "sketch", "slipper", "smolder", "spatula", "spindle",
    "spiral", "sponge", "sprocket", "squirrel", "stadium", "stencil",
    "stirrup", "stucco", "sugar", "summit", "sunset", "sushi", "sweater",
    "syrup", "tablet", "tadpole", "tandem", "tangerine", "tapestry",
    "teapot", "tempo", "tender", "terrace", "textile", "thermal", "thicket",
    "thimble", "thunder", "timber", "tinsel", "toaster", "tomato", "topaz",
    "torch", "tornado", "tortoise", "totem", "tower", "tractor", "trapeze",
    "treasure", "trellis", "triangle", "tricycle", "trombone", "trophy",
    "trumpet", "tulip", "tundra", "tunnel", "turban", "turquoise", "turtle",
    "tuxedo", "ukulele", "umbrella", "unicorn", "uniform", "utensil",
    "valley", "vanilla", "velvet", "vendor", "venture", "vessel", "village",
    "vinegar", "violet", "vulture", "waffle", "wagon", "walnut", "walrus",
    "wander", "wasabi", "waterfall", "weasel", "welcome", "whisker",
    "whistle", "window", "winter", "wisdom", "wombat", "wooden", "yellow",
    "yodel", "yogurt", "zebra", "zenith", "zephyr", "zigzag", "zipper",
)


def validate_pool() -> None:
    """Raise if the pool has duplicates or unusable entries.

    Called by the tests; cheap enough to assert rather than trust a
    hand-maintained literal.
    """
    if len(WORDS) != len(set(WORDS)):
        seen: set[str] = set()
        dupes = sorted({w for w in WORDS if w in seen or seen.add(w)})  # type: ignore[func-returns-value]
        raise ValueError(f"duplicate words in pool: {dupes}")
    bad = [w for w in WORDS if not w.isalpha() or not w.islower() or len(w) < 4]
    if bad:
        raise ValueError(f"unusable words in pool: {bad}")
