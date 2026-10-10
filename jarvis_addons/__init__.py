"""JARVIS add-ons for a multi-owner world (each installation = one owner, own data). Everything here is separate from
live_cmd_console.py; the add-ons that need to hook into a running JARVIS are installed by install(host)."""


def install(host):
    """Called once with JARVIS's module globals. Every add-on installs itself independently; a broken one never stops JARVIS."""
    done = []
    for name in ("perf", "power", "phone_compat", "pattern_lock", "usage_log", "voice_ort", "phone_link", "phone_battery", "telegram_bot", "capability", "dashboard", "network_discovery", "contact_sync"):
        try:
            mod = __import__(f"{__name__}.{name}", fromlist=["install"])
            if mod.install(host):
                done.append(name)
        except ImportError:
            pass                                    # that add-on is not part of this copy
        except Exception as e:
            try:
                host["log_debug"](f"jarvis_addons/{name}", e)
            except Exception:
                pass
    return done
