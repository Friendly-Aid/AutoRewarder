"""
Main entry point for the AutoRewarder application.

By default this script launches the desktop GUI (pywebview). When invoked
with the `--headless` flag — typically by the OS-level autostart entry
that the "Start with Windows/Linux" toggle installs — it delegates to
`AutoRewarder_CLI.main()`, which drives scheduled runs for every enabled
account without bringing up a window.

Usage:
    # GUI:
    python AutoRewarder.py

    # Headless / scheduled:
    python AutoRewarder.py --headless [--account <id-or-label>]
"""

import argparse
import os
import sys

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--headless", action="store_true", help="Run in headless/background mode"
    )
    args, _ = parser.parse_known_args()

    # Delegate to the CLI runner when --headless is set. Any remaining args
    # (e.g., --account, --pc, --mobile) stay in sys.argv so the CLI parser
    # can consume them.
    if args.headless:
        if "--headless" in sys.argv:
            sys.argv.remove("--headless")

        from AutoRewarder_CLI import main as headless_main

        headless_main()
        sys.exit(0)

    # GUI path — import webview + the API lazily so the headless path doesn't
    # pay for the pywebview import cost.
    import webview

    from src.api import AutoRewarderAPI
    from src.config import GUI_DIR, ASSETS_DIR

    api = AutoRewarderAPI()
    window = webview.create_window(
        title="AutoRewarder",
        url=os.path.join(GUI_DIR, "index.html"),
        js_api=api,
        width=640,
        height=680,
        resizable=False,
        background_color="#0b0d12",
    )
    api.set_window(window)  # pass window reference to AutoRewarderAPI for logging

    allow_exit = {"value": False}

    def _install_tray(app_window):
        try:
            import pystray  # type: ignore
            from PIL import Image
        except Exception as e:
            print(f"[WARNING] Tray disabled: {e}")
            return None

        def on_closing():
            if allow_exit["value"]:
                return True
            try:
                app_window.hide()
            except Exception:
                pass
            return False

        def on_show(icon, item):
            try:
                app_window.show()
            except Exception:
                pass

        def on_exit(icon, item):
            allow_exit["value"] = True
            try:
                icon.stop()
            except Exception:
                pass
            try:
                app_window.destroy()
            except Exception:
                pass

        try:
            image = Image.open(os.path.join(ASSETS_DIR, "icon.ico"))
        except Exception:
            image = Image.new("RGB", (64, 64), (0, 0, 0))

        menu = pystray.Menu(
            pystray.MenuItem("Open", on_show, default=True),
            pystray.MenuItem("Exit", on_exit),
        )
        icon = pystray.Icon("AutoRewarder", image, "AutoRewarder", menu)
        app_window.events.closing += on_closing
        icon.run_detached()
        return icon

    _tray_icon = _install_tray(window)
    webview.start(icon=os.path.join(ASSETS_DIR, "icon.ico"))
