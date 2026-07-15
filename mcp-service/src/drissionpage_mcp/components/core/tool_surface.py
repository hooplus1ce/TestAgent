"""Session-scoped tool visibility controls for progressive disclosure."""

from fastmcp.dependencies import CurrentContext
from fastmcp.server.context import Context
from fastmcp.tools import tool

from drissionpage_mcp.core import tool_metadata


@tool(
    name="activate_tool_groups",
    tags={"cap:core", "cap:legacy", "surface:control", "risk:read"},
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def activate_tool_groups(
    groups: list[str],
    reset: bool = False,
    ctx: Context = CurrentContext(),
) -> dict:
    """按当前测试阶段启用 capability；reset=true 恢复服务默认工具面。

    可用分组：core、vtable、legacy、filter、observe、network、workflow、
    storage、roles、devtools。core 始终保留，变更仅影响当前 Agent 会话。
    """
    if reset:
        await ctx.reset_visibility()
        return {
            "ok": True,
            "reset": True,
            "enabled_groups": sorted(tool_metadata.ENABLED_CAPS),
        }

    unknown = sorted(set(groups) - set(tool_metadata.CAP_GROUPS))
    if unknown:
        return {
            "ok": False,
            "reason": "unknown capability groups",
            "unknown": unknown,
            "available": sorted(tool_metadata.CAP_GROUPS),
        }

    selected = set(groups) | {"core"}
    all_tags = {f"cap:{name}" for name in tool_metadata.CAP_GROUPS}
    selected_tags = {f"cap:{name}" for name in selected}
    await ctx.disable_components(tags=all_tags, components={"tool"})
    await ctx.enable_components(tags=selected_tags, components={"tool"})
    return {
        "ok": True,
        "reset": False,
        "enabled_groups": sorted(selected),
    }
