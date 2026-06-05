"""Generate Nature-style architecture figures for Shopping-Agent.

Follows the nature-figure skill contract:
  - matplotlib + sans-serif (Arial)
  - White background, minimal spines
  - Nature PALETTE colors (blue_main, teal, violet, red_strong, green_3)
  - 600 DPI TIFF + SVG + PDF + PNG
"""

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# ── Nature rcParams ──────────────────────────────────────────────────
mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "font.size": 8,
    "axes.spines.right": False,
    "axes.spines.top": False,
    "axes.linewidth": 0.8,
    "legend.frameon": False,
    "figure.facecolor": "white",
})

# ── Nature PALETTE ───────────────────────────────────────────────────
P = {
    "blue_main":   "#0F4D92",
    "blue_sec":    "#3775BA",
    "blue_light":  "#C5D9F0",
    "green":       "#2E9E44",
    "green_light": "#DDF3DE",
    "red":         "#B64342",
    "red_light":   "#F6CFCB",
    "teal":        "#42949E",
    "teal_light":  "#D5EEEE",
    "violet":      "#9A4D8E",
    "violet_light":"#EDE0EB",
    "orange":      "#E8873D",
    "orange_light":"#FDE8D0",
    "neutral":     "#767676",
    "neutral_light":"#EEEEEE",
    "black":       "#272727",
    "white":       "#FFFFFF",
}


def _box(ax, x, y, w, h, text, color, text_color="white", fontsize=8, sub=None, rounded=True):
    """Draw a rounded rectangle with centered text."""
    style = "round,pad=0.02" if rounded else "square,pad=0"
    box = FancyBboxPatch((x, y), w, h, boxstyle=style,
                         facecolor=color, edgecolor="none", linewidth=0)
    ax.add_patch(box)
    ax.text(x + w/2, y + h/2 + (0.008 if sub else 0), text,
            ha="center", va="center", fontsize=fontsize, fontweight="bold",
            color=text_color, zorder=5)
    if sub:
        ax.text(x + w/2, y + h/2 - 0.022, sub,
                ha="center", va="center", fontsize=6, color=text_color, alpha=0.85, zorder=5)


def _arrow(ax, x1, y1, x2, y2, color="#767676", style="-|>", lw=1.2, ls="-"):
    """Draw an arrow between two points."""
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color, lw=lw, linestyle=ls),
                zorder=3)


def _layer_bg(ax, y_bottom, y_top, color, label, label_x=0.02):
    """Draw a layer background band."""
    ax.axhspan(y_bottom, y_top, facecolor=color, edgecolor="none", alpha=0.35, zorder=0)
    ax.text(label_x, y_top - 0.015, label, fontsize=7, fontweight="bold",
            color=P["neutral"], va="top", ha="left", zorder=1)


# =====================================================================
# Figure 1: Architecture Overview
# =====================================================================
def fig_architecture():
    fig, ax = plt.subplots(figsize=(8.5, 6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Title
    ax.text(0.5, 0.97, "Shopping-Agent: VLM-Primary + Action Library Architecture",
            ha="center", va="top", fontsize=11, fontweight="bold", color=P["black"])

    # Layer backgrounds
    _layer_bg(ax, 0.58, 0.92, P["violet_light"], "STRATEGIC LAYER (VLM)")
    _layer_bg(ax, 0.32, 0.56, P["green_light"], "TACTICAL LAYER (Action Library)")
    _layer_bg(ax, 0.15, 0.30, P["orange_light"], "EXECUTION LAYER (Device)")
    _layer_bg(ax, 0.02, 0.13, P["teal_light"], "MEMORY LAYER")

    # ── Strategic Layer ──
    # User Task
    _box(ax, 0.38, 0.88, 0.24, 0.04, "User Task", P["blue_main"], fontsize=9)

    # VLM Pre-Plan
    _box(ax, 0.15, 0.78, 0.20, 0.05, "VLM Pre-Plan", P["violet"], sub="Strong VLM")
    # TaskPlan
    _box(ax, 0.40, 0.78, 0.18, 0.05, "TaskPlan", P["violet"], sub="Steps + Slots")
    # Clarify Agent
    _box(ax, 0.63, 0.78, 0.20, 0.05, "Clarify Agent", P["violet"], sub="3-Layer Filter")

    # Decision diamond (simplified as box with different color)
    _box(ax, 0.36, 0.66, 0.28, 0.06, "_needs_vlm?  Dual-Speed", P["orange"], fontsize=8)

    # Fast Path
    _box(ax, 0.04, 0.60, 0.26, 0.05, "Fast Path  ~0.5s", P["green"], sub="Grounded Action Execute")
    # Full Path
    _box(ax, 0.70, 0.60, 0.26, 0.05, "Full Path  ~5s", P["blue_main"], sub="VLM + Action Grounding")

    # Arrows: User → Pre-Plan
    _arrow(ax, 0.50, 0.88, 0.25, 0.83, P["blue_main"])
    # Pre-Plan → TaskPlan
    _arrow(ax, 0.35, 0.805, 0.40, 0.805, P["violet"])
    # Decision → Fast Path
    _arrow(ax, 0.36, 0.69, 0.17, 0.65, P["green"], lw=1.8)
    ax.text(0.24, 0.695, "Grounded", fontsize=6.5, color=P["green"], fontweight="bold")
    # Decision → Full Path
    _arrow(ax, 0.64, 0.69, 0.83, 0.65, P["blue_main"], lw=1.8)
    ax.text(0.72, 0.695, "Semantic", fontsize=6.5, color=P["blue_main"], fontweight="bold")

    # SpecGuard
    _box(ax, 0.78, 0.54, 0.15, 0.03, "SpecGuard", P["red"], fontsize=7)

    # ── Tactical Layer ──
    _box(ax, 0.04, 0.43, 0.25, 0.06, "Action Advisor", P["teal"],
         sub="query · try_ground · format")
    # Neo4j
    _box(ax, 0.34, 0.43, 0.20, 0.06, "Neo4j", "#2E7D32",
         sub="Action Library (Graph DB)")
    # Edge Lifecycle
    _box(ax, 0.60, 0.43, 0.22, 0.06, "Edge Lifecycle", P["teal"],
         sub="hypothesis → promoted")

    # Graph schema mini
    _box(ax, 0.10, 0.34, 0.11, 0.04, "UIState", P["violet"], fontsize=7)
    _box(ax, 0.28, 0.34, 0.10, 0.04, "Action", P["orange"], fontsize=7)
    _box(ax, 0.44, 0.34, 0.11, 0.04, "UIState", P["violet"], fontsize=7)
    _arrow(ax, 0.21, 0.36, 0.28, 0.36, P["orange"], lw=1)
    _arrow(ax, 0.38, 0.36, 0.44, 0.36, P["orange"], lw=1)

    # Advisor → Fast Path
    _arrow(ax, 0.17, 0.49, 0.17, 0.60, P["green"], lw=1, ls="--")
    ax.text(0.08, 0.545, "ActionHints", fontsize=6, color=P["green"], rotation=90)
    # Advisor → Full Path (try_ground)
    _arrow(ax, 0.29, 0.46, 0.76, 0.60, P["teal"], lw=0.8, ls="--")
    ax.text(0.55, 0.55, "try_ground", fontsize=6, color=P["teal"])

    # ── Execution Layer ──
    for i, (label, sub) in enumerate([
        ("Screenshot", "ADB/HDC/iOS"),
        ("Action Execute", "Tap·Type·Swipe"),
        ("PageClassifier", "VLM Page Detection"),
        ("Verification", "CAPTCHA/Login"),
    ]):
        x = 0.04 + i * 0.24
        _box(ax, x, 0.18, 0.20, 0.06, label, P["orange"], sub=sub, fontsize=7)

    # ── Memory Layer ──
    for i, (label, sub) in enumerate([
        ("FAISS", "User Prefs"),
        ("Session State", "Products·Steps"),
        ("Step Summaries", "Compressed History"),
        ("Retrieval GW", "On-Demand"),
    ]):
        x = 0.04 + i * 0.22
        _box(ax, x, 0.04, 0.18, 0.05, label, "#42949E", sub=sub, fontsize=7)

    # Self-evolution arrow
    _arrow(ax, 0.44, 0.24, 0.44, 0.43, P["violet"], lw=1.2, ls="--")
    ax.text(0.45, 0.30, "record_observation\n(self-evolution)", fontsize=5.5,
            color=P["violet"], va="center")

    # Legend
    legend_items = [
        mpatches.Patch(facecolor=P["blue_main"], label="VLM Decision / Full Path"),
        mpatches.Patch(facecolor=P["green"], label="Graph Advisory / Fast Path"),
        mpatches.Patch(facecolor=P["orange"], label="Device / Execution"),
        mpatches.Patch(facecolor=P["violet"], label="Self-Evolution / Lifecycle"),
    ]
    ax.legend(handles=legend_items, loc="lower right", fontsize=6.5,
              ncol=2, bbox_to_anchor=(0.98, 0.00))

    fig.savefig("phone_agent/docs/architecture-overview.png", dpi=300, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    fig.savefig("phone_agent/docs/architecture-overview.svg", bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    print("✅ architecture-overview.png / .svg")


# =====================================================================
# Figure 2: Graph Schema
# =====================================================================
def fig_graph_schema():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4.5),
                                    gridspec_kw={"width_ratios": [1.2, 1]})
    fig.suptitle("Neo4j Graph Schema: UIState → Action → UIState",
                 fontsize=11, fontweight="bold", color=P["black"], y=0.98)

    # ── Left panel: Schema diagram ──
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.axis("off")
    ax1.set_title("a", fontsize=10, fontweight="bold", loc="left", pad=8)

    # UIState (source)
    _box(ax1, 0.02, 0.55, 0.28, 0.38, "", P["violet_light"], text_color=P["black"])
    ax1.add_patch(FancyBboxPatch((0.02, 0.85), 0.28, 0.08, boxstyle="round,pad=0.01",
                                  facecolor=P["violet"], edgecolor="none"))
    ax1.text(0.16, 0.89, "UIState (Source)", ha="center", fontsize=8,
             fontweight="bold", color="white")
    props_src = ["state_id: string", "app: '淘宝' | '京东'",
                 "page_type: ShoppingPageType", "summary: ≤15 chars",
                 "risk_level: normal|medium|high", "semantic_signature: string"]
    for i, p in enumerate(props_src):
        ax1.text(0.05, 0.80 - i*0.04, p, fontsize=6, family="monospace", color=P["black"])

    # Action (center)
    _box(ax1, 0.36, 0.45, 0.28, 0.48, "", P["orange_light"], text_color=P["black"])
    ax1.add_patch(FancyBboxPatch((0.36, 0.85), 0.28, 0.08, boxstyle="round,pad=0.01",
                                  facecolor=P["orange"], edgecolor="none"))
    ax1.text(0.50, 0.89, "Action", ha="center", fontsize=8,
             fontweight="bold", color="white")
    props_act = ["type: Tap|Type|Swipe|Back", "region: top_center|...",
                 "semantic_target: string", "target_locator: {element:[x,y]}",
                 "expected_postcondition", "lifecycle_stage: string",
                 "verification_count: int", "dominance_ratio: float",
                 "outcome_entropy: float", "outcome_distribution: {}"]
    for i, p in enumerate(props_act):
        ax1.text(0.39, 0.80 - i*0.035, p, fontsize=5.5, family="monospace", color=P["black"])

    # UIState (target)
    _box(ax1, 0.70, 0.65, 0.28, 0.28, "", P["violet_light"], text_color=P["black"])
    ax1.add_patch(FancyBboxPatch((0.70, 0.85), 0.28, 0.08, boxstyle="round,pad=0.01",
                                  facecolor=P["violet"], edgecolor="none"))
    ax1.text(0.84, 0.89, "UIState (Target)", ha="center", fontsize=8,
             fontweight="bold", color="white")
    props_tgt = ["state_id: string", "page_type: string", "..."]
    for i, p in enumerate(props_tgt):
        ax1.text(0.73, 0.80 - i*0.04, p, fontsize=6, family="monospace", color=P["black"])

    # Relationship arrows
    _arrow(ax1, 0.30, 0.82, 0.36, 0.82, P["blue_main"], lw=2)
    ax1.text(0.33, 0.84, "NEXT_ACTION", fontsize=5.5, color=P["blue_main"],
             fontweight="bold", ha="center")
    _arrow(ax1, 0.64, 0.82, 0.70, 0.82, P["orange"], lw=2)
    ax1.text(0.67, 0.84, "PRODUCES", fontsize=5.5, color=P["orange"],
             fontweight="bold", ha="center")

    # Grounded section
    ax1.add_patch(FancyBboxPatch((0.02, 0.02), 0.45, 0.35, boxstyle="round,pad=0.02",
                                  facecolor=P["green_light"], edgecolor=P["green"],
                                  linewidth=0.8, linestyle="--"))
    ax1.text(0.04, 0.34, "GROUNDED (Fast Path)", fontsize=7, fontweight="bold", color=P["green"])
    grounded = ["home → search_input  (Tap search bar)",
                "search_input → search_result  (Compound)",
                "any → home  (Home button)", "any → previous  (Back)"]
    for i, t in enumerate(grounded):
        ax1.text(0.05, 0.28 - i*0.06, t, fontsize=6, family="monospace", color=P["black"])

    # Ungrounded section
    ax1.add_patch(FancyBboxPatch((0.52, 0.02), 0.46, 0.35, boxstyle="round,pad=0.02",
                                  facecolor=P["blue_light"], edgecolor=P["blue_main"],
                                  linewidth=0.8, linestyle="--"))
    ax1.text(0.54, 0.34, "UNGROUNDED (Full Path → VLM)", fontsize=7,
             fontweight="bold", color=P["blue_main"])
    ungrounded = ["search_result → product_detail",
                  "product_detail → spec_selection",
                  "spec_selection → cart/checkout",
                  "search_result → store"]
    for i, t in enumerate(ungrounded):
        ax1.text(0.55, 0.28 - i*0.06, t, fontsize=6, family="monospace", color=P["black"])

    # ── Right panel: Page type flow ──
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.axis("off")
    ax2.set_title("b", fontsize=10, fontweight="bold", loc="left", pad=8)
    ax2.text(0.5, 0.95, "Shopping Flow", ha="center", fontsize=9, fontweight="bold")

    pages = [
        ("home", 0.5, 0.85),
        ("search_input", 0.5, 0.72),
        ("search_result", 0.5, 0.59),
        ("product_detail", 0.5, 0.46),
        ("spec_selection", 0.5, 0.33),
        ("cart", 0.25, 0.20),
        ("checkout", 0.75, 0.20),
    ]
    for name, x, y in pages:
        color = P["green"] if name in ("home", "search_input") else P["blue_main"]
        _box(ax2, x - 0.18, y - 0.03, 0.36, 0.06, name, color, fontsize=7.5)

    # Arrows
    flow = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (4, 6)]
    for i, j in flow:
        _, x1, y1 = pages[i]
        _, x2, y2 = pages[j]
        is_ungrounded = (i, j) in [(2, 3), (3, 4), (4, 5), (4, 6)]
        color = P["blue_main"] if is_ungrounded else P["green"]
        ls = "-" if is_ungrounded else "--"
        _arrow(ax2, x1, y1 - 0.03, x2, y2 + 0.03, color, lw=1.2, ls=ls)

    # Legend
    ax2.text(0.5, 0.08, "── Grounded (graph)    ── Ungrounded (VLM)", ha="center",
             fontsize=6.5, color=P["neutral"])

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig("phone_agent/docs/graph-schema.png", dpi=300, bbox_inches="tight",
                facecolor="white")
    fig.savefig("phone_agent/docs/graph-schema.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("✅ graph-schema.png / .svg")


# =====================================================================
# Figure 3: Edge Lifecycle State Machine
# =====================================================================
def fig_lifecycle():
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.97, "Edge Lifecycle: Self-Evolution State Machine",
            ha="center", va="top", fontsize=11, fontweight="bold", color=P["black"])

    # States
    states = [
        ("hypothesis", 0.10, 0.65, P["neutral"]),
        ("candidate",  0.35, 0.65, P["orange"]),
        ("promoted",   0.60, 0.65, P["green"]),
        ("demoted",    0.85, 0.65, P["red"]),
    ]
    for name, x, y, color in states:
        _box(ax, x - 0.10, y - 0.04, 0.20, 0.08, name, color, fontsize=9)

    # Transitions
    _arrow(ax, 0.20, 0.67, 0.25, 0.67, P["blue_main"], lw=2)
    ax.text(0.225, 0.72, "verifications\n≥ min_count", fontsize=6, ha="center",
            color=P["blue_main"], va="bottom")

    _arrow(ax, 0.45, 0.67, 0.50, 0.67, P["blue_main"], lw=2)
    ax.text(0.475, 0.72, "dominance\n≥ 80%", fontsize=6, ha="center",
            color=P["blue_main"], va="bottom")

    _arrow(ax, 0.70, 0.67, 0.75, 0.67, P["red"], lw=2)
    ax.text(0.725, 0.72, "dominance\n< 40%", fontsize=6, ha="center",
            color=P["red"], va="bottom")

    # Re-explore loop
    ax.annotate("", xy=(0.10, 0.57), xytext=(0.85, 0.57),
                arrowprops=dict(arrowstyle="-|>", color=P["violet"], lw=1.2,
                                linestyle="--",
                                connectionstyle="arc3,rad=0.25"))
    ax.text(0.48, 0.47, "VLM re-explores → new observations restart cycle",
            fontsize=6.5, ha="center", color=P["violet"], style="italic")

    # Annotation boxes
    annotations = [
        ("hypothesis", "1–2 observations\nNot visible to ActionAdvisor\nVLM explores independently",
         0.10, 0.25, P["neutral"]),
        ("candidate", "Unstable outcomes\nLow-confidence hint\nShannon entropy > threshold",
         0.35, 0.25, P["orange"]),
        ("promoted", "Verified & reliable\nGrounded → Fast Path\nUngrounded → VLM hint\nPersisted to Neo4j",
         0.60, 0.25, P["green"]),
        ("demoted", "UI layout changed\nRemoved from hints\nVLM re-explores",
         0.85, 0.25, P["red"]),
    ]
    for title, text, x, y, color in annotations:
        ax.add_patch(FancyBboxPatch((x - 0.11, y - 0.14), 0.22, 0.26,
                                     boxstyle="round,pad=0.02",
                                     facecolor="white", edgecolor=color,
                                     linewidth=1.0))
        ax.text(x, y + 0.08, title, fontsize=7, fontweight="bold", ha="center",
                color=color)
        for i, line in enumerate(text.split("\n")):
            ax.text(x, y + 0.02 - i * 0.045, line, fontsize=6, ha="center",
                    color=P["black"])

    # SAVA config box
    ax.add_patch(FancyBboxPatch((0.15, 0.01), 0.70, 0.06, boxstyle="round,pad=0.01",
                                 facecolor=P["neutral_light"], edgecolor=P["neutral"],
                                 linewidth=0.5))
    ax.text(0.50, 0.04, "SAVA Config:  min_verification = 3  |  dominance_threshold = 0.80  |  "
            "entropy_vlm_threshold = 0.50  |  demotion_threshold = 0.40",
            fontsize=6, ha="center", color=P["black"], family="monospace")

    fig.savefig("phone_agent/docs/lifecycle.png", dpi=300, bbox_inches="tight",
                facecolor="white")
    fig.savefig("phone_agent/docs/lifecycle.svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print("✅ lifecycle.png / .svg")


if __name__ == "__main__":
    fig_architecture()
    fig_graph_schema()
    fig_lifecycle()
    print("\nAll figures generated in phone_agent/docs/")
