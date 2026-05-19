"""Network-allowlist slash commands.

Three commands wrapping :class:`clawmes.services.endpoint_allowlist.EndpointAllowlistService`:

  * ``/allowlist`` — show the full default + user-added host list plus
    the most recent blocked attempts (audit ring).
  * ``/allow <host>`` — add a host to the session allowlist.
  * ``/disallow <host>`` — remove a previously-allowed host.

Defaults (from ``clawmes.lib.http._DEFAULT_ALLOWLIST``) are not
removable through this surface — they live in source and require a
code change to modify. Session-scoped exceptions reset on restart.
"""

from __future__ import annotations


async def handle_allowlist(raw_args: str) -> str:
    from clawmes.lib.http import _DEFAULT_ALLOWLIST
    from clawmes.services.endpoint_allowlist import get_endpoint_allowlist_service

    svc = get_endpoint_allowlist_service()
    defaults = sorted(_DEFAULT_ALLOWLIST)
    user = sorted(svc.list_user_hosts())
    blocks = svc.recent_blocks(limit=10)

    lines = [
        f"Network allowlist ({len(defaults)} default + {len(user)} user-added):",
        "",
        "Defaults (always allowed; change requires a code edit):",
    ]
    for host in defaults:
        lines.append(f"  + {host}")

    if user:
        lines.append("")
        lines.append("User-added (this session — resets on restart):")
        for host in user:
            lines.append(f"  + {host}")

    if blocks:
        lines.append("")
        lines.append(f"Recent blocked attempts (last {len(blocks)}):")
        for block in blocks:
            url = block["url"]
            if len(url) > 80:
                url = url[:77] + "..."
            lines.append(f"  x {block['host']} -> {url}")
    else:
        lines.append("")
        lines.append("No blocked attempts recorded this session.")

    return "\n".join(lines)


async def handle_allow(raw_args: str) -> str:
    from clawmes.services.endpoint_allowlist import get_endpoint_allowlist_service

    host = raw_args.strip()
    if not host:
        return (
            "Add a host to the session allowlist.\n"
            "Usage: /allow <host>\n"
            "Example: /allow api.example.com\n\n"
            "Session-scoped: removes on restart. For permanent additions, "
            "edit clawmes.network_allowlist.extra_hosts in config.yaml."
        )

    svc = get_endpoint_allowlist_service()
    try:
        added = svc.add_host(host)
    except ValueError as exc:
        return f"Cannot add host: {exc}"
    if added:
        return f"Added {host!r} to the session allowlist. Removes on restart, or via /disallow."
    return f"{host!r} is already in the user allowlist."


async def handle_disallow(raw_args: str) -> str:
    from clawmes.services.endpoint_allowlist import get_endpoint_allowlist_service

    host = raw_args.strip()
    if not host:
        return (
            "Remove a host from the user allowlist.\n"
            "Usage: /disallow <host>\n\n"
            "Defaults aren't removable through this command — they live in "
            "clawmes.lib.http._DEFAULT_ALLOWLIST and require a code change."
        )

    svc = get_endpoint_allowlist_service()
    removed = svc.remove_host(host)
    if removed:
        return f"Removed {host!r} from the user allowlist."
    return (
        f"{host!r} not in the user allowlist. Defaults can't be removed "
        "through /disallow — see clawmes.lib.http._DEFAULT_ALLOWLIST."
    )


def register(ctx) -> None:
    """Wire allowlist commands into Hermes."""
    ctx.register_command(
        name="allowlist",
        handler=handle_allowlist,
        description="Show network allowlist + recent blocked attempts",
    )
    ctx.register_command(
        name="allow",
        handler=handle_allow,
        description="Add a host to the session allowlist",
        args_hint="<host>",
    )
    ctx.register_command(
        name="disallow",
        handler=handle_disallow,
        description="Remove a host from the session allowlist",
        args_hint="<host>",
    )
