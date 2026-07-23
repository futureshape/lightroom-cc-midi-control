"""
Lightroom CC parameter & action catalogue.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class LRParam:
    name: str            # Lightroom internal identifier
    label: str           # Human-readable label
    min_val: float
    max_val: float
    default: float
    category: str
    sensitivity: float   # Per-tick delta used in relative encoder mode


# ---------------------------------------------------------------------------
# Develop sliders
# ---------------------------------------------------------------------------
PARAMETERS: list[LRParam] = [
    # ── Light ──────────────────────────────────────────────────────────────
    LRParam("Exposure",              "Exposure",           -5.0,  5.0,    0.0, "Light",   0.05),
    LRParam("Contrast",              "Contrast",         -100,  100,      0,   "Light",   1.0),
    LRParam("Highlights",            "Highlights",       -100,  100,      0,   "Light",   1.0),
    LRParam("Shadows",               "Shadows",          -100,  100,      0,   "Light",   1.0),
    LRParam("Whites",                "Whites",           -100,  100,      0,   "Light",   1.0),
    LRParam("Blacks",                "Blacks",           -100,  100,      0,   "Light",   1.0),
    LRParam("Texture",               "Texture",          -100,  100,      0,   "Light",   1.0),
    LRParam("Clarity",               "Clarity",          -100,  100,      0,   "Light",   1.0),
    LRParam("Dehaze",                "Dehaze",           -100,  100,      0,   "Light",   1.0),
    # ── Color ──────────────────────────────────────────────────────────────
    LRParam("Vibrance",              "Vibrance",         -100,  100,      0,   "Color",   1.0),
    LRParam("Saturation",            "Saturation",       -100,  100,      0,   "Color",   1.0),
    LRParam("Temperature",           "Temperature",      2000, 50000,  5000,   "Color", 200.0),
    LRParam("Tint",                  "Tint",             -150,  150,      0,   "Color",   2.0),
    LRParam("ShadowTint",            "Shadow Tint",      -100,  100,      0,   "Color",   1.0),
    # ── Detail ─────────────────────────────────────────────────────────────
    LRParam("Sharpness",             "Sharpening Amount",   0,  150,     40,   "Detail",  1.0),
    LRParam("SharpenRadius",         "Sharpening Radius",   0,    3,    1.0,   "Detail",  0.05),
    LRParam("SharpenDetail",         "Sharpening Detail",   0,  100,     25,   "Detail",  1.0),
    LRParam("SharpenEdgeMasking",    "Sharpening Masking",  0,  100,      0,   "Detail",  1.0),
    LRParam("LuminanceSmoothing",    "Luminance NR",        0,  100,      0,   "Detail",  1.0),
    LRParam("ColorNoiseReduction",   "Color NR",            0,  100,     25,   "Detail",  1.0),
    # ── Effects ────────────────────────────────────────────────────────────
    LRParam("PostCropVignetteAmount","Vignette Amount",   -100,  100,     0,   "Effects", 1.0),
    LRParam("PostCropVignetteMidpoint","Vignette Midpoint",0, 100,       50,   "Effects", 1.0),
    LRParam("PostCropVignetteFeather","Vignette Feather",  0,  100,     50,   "Effects", 1.0),
    LRParam("GrainAmount",           "Grain Amount",        0,  100,      0,   "Effects", 1.0),
    LRParam("GrainSize",             "Grain Size",          0,  100,     25,   "Effects", 1.0),
    LRParam("GrainFrequency",        "Grain Roughness",     0,  100,     50,   "Effects", 1.0),
]

# ---------------------------------------------------------------------------
# One-shot actions  (name, label, category)
# ---------------------------------------------------------------------------
ACTIONS: list[tuple[str, str, str]] = [
    # navigation
    ("nextPhoto",                    "Next Photo",              "navigation"),
    ("previousPhoto",                "Previous Photo",          "navigation"),
    ("goBack",                       "Go Back",                 "navigation"),
    ("goForward",                    "Go Forward",              "navigation"),
    # editing
    ("setAutoTone",                  "Auto Tone",               "editing"),
    ("resetAllDevelopAdjustments",   "Reset All Edits",         "editing"),
    ("undo",                         "Undo",                    "editing"),
    ("redo",                         "Redo",                    "editing"),
    ("toggleBlackAndWhite",          "Toggle B&W",              "editing"),
    ("copyEditSettings",             "Copy Edit Settings",      "editing"),
    ("pasteEditSettings",            "Paste Edit Settings",     "editing"),
    # view
    ("zoomIn",                       "Zoom In",                 "view"),
    ("zoomOut",                      "Zoom Out",                "view"),
    ("zoomToFit",                    "Zoom to Fit",             "view"),
    ("zoomToOneToOne",               "Zoom 1:1",                "view"),
    ("toggleZoom",                   "Toggle Zoom",             "view"),
    ("revealAdjustedControls",       "Reveal Adjusted Controls","view"),
    # flag / rating
    ("flagPick",                     "Flag: Pick",              "flag & rating"),
    ("flagReject",                   "Flag: Reject",            "flag & rating"),
    ("flagUnflag",                   "Unflag",                  "flag & rating"),
    ("flagPickToggle",               "Toggle Pick Flag",        "flag & rating"),
    ("flagRejectToggle",             "Toggle Reject Flag",      "flag & rating"),
    ("ratingIncrease",               "Rating +1",               "flag & rating"),
    ("ratingDecrease",               "Rating −1",               "flag & rating"),
    ("rating0",                      "Set Rating 0",            "flag & rating"),
    ("rating1",                      "Set Rating 1",            "flag & rating"),
    ("rating2",                      "Set Rating 2",            "flag & rating"),
    ("rating3",                      "Set Rating 3",            "flag & rating"),
    ("rating4",                      "Set Rating 4",            "flag & rating"),
    ("rating5",                      "Set Rating 5",            "flag & rating"),
    # color labels
    ("colorLabelRed",                "Color Label: Red",        "color label"),
    ("colorLabelYellow",             "Color Label: Yellow",     "color label"),
    ("colorLabelGreen",              "Color Label: Green",      "color label"),
    ("colorLabelBlue",               "Color Label: Blue",       "color label"),
    ("colorLabelPurple",             "Color Label: Purple",     "color label"),
    ("colorLabelNone",               "Color Label: None",       "color label"),
]

# Ordered unique category lists
PARAM_CATEGORIES:  list[str] = list(dict.fromkeys(p.category for p in PARAMETERS))
ACTION_CATEGORIES: list[str] = list(dict.fromkeys(c for _, _, c in ACTIONS))


def params_by_category(category: str) -> list[LRParam]:
    return [p for p in PARAMETERS if p.category == category]


def actions_by_category(category: str) -> list[tuple[str, str]]:
    return [(n, lbl) for n, lbl, c in ACTIONS if c == category]


def get_param(name: str) -> Optional[LRParam]:
    return next((p for p in PARAMETERS if p.name == name), None)
