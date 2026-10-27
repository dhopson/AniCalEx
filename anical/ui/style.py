"""Application stylesheet. Colors come from libadwaita variables, so light and dark styles both work."""

from __future__ import annotations

import importlib.resources

from gi.repository import Gdk, Gtk

from anical import APP_ID

_CSS = """
.cover { border-radius: 6px; }
.cover-placeholder {
  border-radius: 6px;
  background-color: color-mix(in srgb, currentColor 8%, transparent);
  font-weight: bold;
}

/* Calendar entry status: upcoming = accent, aired = success, delayed = warning,
   watched = faded; estimated times use a dashed edge. */
.cal-card {
  border-left: 4px solid var(--accent-bg-color);
  border-radius: 6px;
  padding: 4px 6px;
  background-color: color-mix(in srgb, currentColor 5%, transparent);
}
.cal-card.just-aired, .cal-card.available { border-left-color: var(--success-bg-color); }
.cal-card.delayed { border-left-color: var(--warning-bg-color); }
.cal-card.watched { border-left-color: color-mix(in srgb, currentColor 25%, transparent); opacity: 0.6; }
.cal-card.estimated { border-left-style: dashed; }

.cal-dot { min-width: 8px; min-height: 8px; border-radius: 4px; background-color: var(--accent-bg-color); }
.cal-dot.just-aired, .cal-dot.available { background-color: var(--success-bg-color); }
.cal-dot.delayed { background-color: var(--warning-bg-color); }
.cal-dot.watched { background-color: color-mix(in srgb, currentColor 25%, transparent); }
.cal-dot.estimated { background-color: transparent; border: 1px dashed var(--accent-bg-color); }

.cal-status {
  border-radius: 999px;
  padding: 2px 8px;
  font-size: smaller;
  font-weight: bold;
  color: var(--accent-color);
  background-color: color-mix(in srgb, var(--accent-bg-color) 15%, transparent);
}
.cal-status.just-aired, .cal-status.available {
  color: var(--success-color);
  background-color: color-mix(in srgb, var(--success-bg-color) 15%, transparent);
}
.cal-status.delayed {
  color: var(--warning-color);
  background-color: color-mix(in srgb, var(--warning-bg-color) 15%, transparent);
}
.cal-status.watched {
  color: inherit;
  opacity: 0.6;
  background-color: color-mix(in srgb, currentColor 8%, transparent);
}

/* Grid lines: the text color at low opacity, so they sit just off the background in both styles. */
.cal-column {
  padding: 0 6px 12px 6px;
  border-left: 1px solid color-mix(in srgb, currentColor 10%, transparent);
}
.cal-column.first { border-left-color: transparent; }

button.cal-cell {
  padding: 4px;
  min-height: 84px;
  border-radius: 0;
  border-right: 1px solid color-mix(in srgb, currentColor 10%, transparent);
  border-bottom: 1px solid color-mix(in srgb, currentColor 10%, transparent);
}
button.cal-cell.first-col { border-left: 1px solid color-mix(in srgb, currentColor 10%, transparent); }
button.cal-cell.first-row { border-top: 1px solid color-mix(in srgb, currentColor 10%, transparent); }
button.cal-cell.compact { min-height: 44px; }
.cal-today { color: var(--accent-color); font-weight: bold; }
.cal-outside { opacity: 0.45; }

/* Setup wizard: the mascot's speech bubble, with a tail pointing down at it. */
.wizard-bubble {
  background-color: var(--card-bg-color);
  color: var(--card-fg-color);
  border-radius: 18px;
  padding: 14px 16px;
  box-shadow: 0 1px 3px color-mix(in srgb, black 18%, transparent);
}
/* A triangle under the bubble, pointing down at the mascot: two half-squares cut on
   the diagonal. GTK ignores negative margins, so it can't overlap the bubble. */
.wizard-bubble-tail {
  min-width: 20px;
  min-height: 10px;
  margin-left: 70px;
  background-image:
    linear-gradient(to top right, transparent 50%, var(--card-bg-color) 50%),
    linear-gradient(to top left, transparent 50%, var(--card-bg-color) 50%);
  background-size: 10px 10px, 10px 10px;
  background-position: left top, right top;
  background-repeat: no-repeat;
}
.wizard-step-dot {
  min-width: 8px;
  min-height: 8px;
  border-radius: 4px;
  background-color: color-mix(in srgb, currentColor 20%, transparent);
}
.wizard-step-dot.current { background-color: var(--accent-bg-color); min-width: 20px; }
.wizard-step-dot.done { background-color: color-mix(in srgb, var(--accent-bg-color) 50%, transparent); }

/* The "Playing episode" card in the window's corner. */
.playing-card {
  background-color: var(--popover-bg-color);
  color: var(--popover-fg-color);
  border-radius: 12px;
  padding: 8px;
  box-shadow: 0 2px 8px color-mix(in srgb, black 30%, transparent);
}
.playing-scene { border-radius: 6px; }
"""


def install_icons() -> None:
    """Make the bundled app icon findable when running from source (installed copies
    live in the system icon theme), and use it for windows."""
    icons = importlib.resources.files("anical") / "data" / "icons"
    Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(str(icons))
    Gtk.Window.set_default_icon_name(APP_ID)


def install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_string(_CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
