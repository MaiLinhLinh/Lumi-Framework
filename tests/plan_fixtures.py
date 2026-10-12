"""Shared Surface Retrieval Plan fixtures for runtime and executor tests."""

from final_framework.surface.contracts import SurfaceDraft
from final_framework.task_manager import SurfacePlanTask, SurfaceRetrievalPlan
from final_framework.panel.contracts import GridRect, PlanBlock


def make_surface_plan(tasks=()) -> SurfaceRetrievalPlan:
    blocks = [PlanBlock(
        widget_id="text", grid=GridRect(1, 1, 16, 1),
        props={"content": "Nội dung kiểm thử", "role": "title"},
    )]
    declared_tasks = []
    for index, (ref, task_type, instruction) in enumerate(tasks):
        slot = f"slot_{ref[2:]}"
        is_information = task_type == "search_information_task"
        blocks.append(PlanBlock(
            widget_id="text" if is_information else "image",
            grid=GridRect(1, 2 + 2 * index, 16, 2),
            props={"role": "body"} if is_information else {},
            slot_role=slot, description=f"Dữ liệu cho {slot}",
        ))
        declared_tasks.append(SurfacePlanTask(ref, task_type, (slot,), instruction))
    return SurfaceRetrievalPlan(tuple(blocks), None, tuple(declared_tasks))


async def submit_plan(manager, plan, *, active_surface=None):
    return await manager.submit_surface_plan(
        plan, draft=SurfaceDraft(blocks=plan.blocks),
        surface_id="panel_test", active_surface=active_surface,
    )
