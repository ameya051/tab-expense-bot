"""Server-side chart generation using matplotlib.

All functions are synchronous and should be called via run_in_threadpool()
from async handlers to avoid blocking the event loop.
"""

import io
from datetime import date

import matplotlib
matplotlib.use("AGG")  # Non-interactive backend — must be set before pyplot import

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from app.services.currency_service import get_currency_symbol


# ---------------------------------------------------------------------------
# Shared style constants
# ---------------------------------------------------------------------------

BG_COLOR = "#1a1a2e"
CARD_COLOR = "#16213e"
TEXT_COLOR = "#e0e0e0"
ACCENT_COLORS = [
    "#e94560", "#0f3460", "#533483", "#48c9b0",
    "#f39c12", "#e74c3c", "#3498db", "#2ecc71",
    "#9b59b6", "#1abc9c", "#e67e22", "#34495e",
]
GRID_COLOR = "#2a2a4a"


def _apply_base_style(fig: plt.Figure, ax: plt.Axes) -> None:
    """Apply the shared dark theme to a figure."""
    fig.set_facecolor(BG_COLOR)
    ax.set_facecolor(CARD_COLOR)
    ax.tick_params(colors=TEXT_COLOR, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID_COLOR)
    ax.spines["bottom"].set_color(GRID_COLOR)
    ax.yaxis.grid(True, color=GRID_COLOR, linestyle="--", alpha=0.5)
    ax.set_axisbelow(True)


# ---------------------------------------------------------------------------
# Category bar chart
# ---------------------------------------------------------------------------

def generate_category_bar_chart(
    data: list[dict],
    period_label: str = "This Month",
    currency: str = "INR",
) -> bytes:
    """Generate a horizontal bar chart of spending by category.

    Args:
        data: List of {"category": str, "total": float} dicts, sorted desc.
        period_label: Label for the chart title (e.g. "April 2026").
        currency: Currency code for axis labels.

    Returns:
        PNG image as raw bytes.
    """
    if not data:
        return _empty_chart("No expenses to show")

    symbol = get_currency_symbol(currency)
    categories = [d["category"].title() for d in reversed(data)]
    amounts = [d["total"] for d in reversed(data)]
    colors = [ACCENT_COLORS[i % len(ACCENT_COLORS)] for i in range(len(categories))]

    fig, ax = plt.subplots(figsize=(10, max(4, len(categories) * 0.7)), dpi=150)
    _apply_base_style(fig, ax)

    bars = ax.barh(categories, amounts, color=colors, height=0.6, edgecolor="none")

    # Add value labels on bars
    for bar, amount in zip(bars, amounts):
        ax.text(
            bar.get_width() + max(amounts) * 0.02,
            bar.get_y() + bar.get_height() / 2,
            f"{symbol}{amount:,.2f}",
            va="center",
            ha="left",
            color=TEXT_COLOR,
            fontsize=10,
            fontweight="bold",
        )

    ax.set_title(
        f"📊 Spending by Category — {period_label}",
        color=TEXT_COLOR,
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlabel(f"Amount ({symbol})", color=TEXT_COLOR, fontsize=11)

    fig.tight_layout(pad=2)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# Trend line chart
# ---------------------------------------------------------------------------

def generate_trend_line_chart(
    data: list[dict],
    period_label: str = "This Month",
    currency: str = "INR",
) -> bytes:
    """Generate a line chart showing daily spending trend.

    Args:
        data: List of {"date": date, "total": float} dicts, sorted chronologically.
        period_label: Label for the chart title.
        currency: Currency code for axis labels.

    Returns:
        PNG image as raw bytes.
    """
    if not data:
        return _empty_chart("No expenses to show")

    symbol = get_currency_symbol(currency)
    dates = [d["date"] for d in data]
    amounts = [d["total"] for d in data]

    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    _apply_base_style(fig, ax)

    ax.plot(
        dates,
        amounts,
        color="#e94560",
        linewidth=2.5,
        marker="o",
        markersize=6,
        markerfacecolor="#ffffff",
        markeredgecolor="#e94560",
        markeredgewidth=2,
    )

    # Fill area under the line
    ax.fill_between(dates, amounts, alpha=0.15, color="#e94560")

    ax.set_title(
        f"📈 Daily Spending Trend — {period_label}",
        color=TEXT_COLOR,
        fontsize=14,
        fontweight="bold",
        pad=15,
    )
    ax.set_xlabel("Date", color=TEXT_COLOR, fontsize=11)
    ax.set_ylabel(f"Amount ({symbol})", color=TEXT_COLOR, fontsize=11)

    # Format x-axis dates
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    fig.autofmt_xdate(rotation=30, ha="right")

    fig.tight_layout(pad=2)
    return _fig_to_bytes(fig)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fig_to_bytes(fig: plt.Figure) -> bytes:
    """Render a matplotlib figure to PNG bytes and close it."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _empty_chart(message: str) -> bytes:
    """Generate a simple placeholder chart with a message."""
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    _apply_base_style(fig, ax)
    ax.text(
        0.5, 0.5, message,
        transform=ax.transAxes,
        ha="center", va="center",
        color=TEXT_COLOR, fontsize=16,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    return _fig_to_bytes(fig)
