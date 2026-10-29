"""Prompt builder for the draw_image task agent."""

def _draw_prompt(*, instruction: str) -> str:
    return (
        "Create exactly one image for an interactive Lumi panel. "
        "Follow the visual instruction faithfully. Do not return explanation, markdown, "
        "a URL, or text-only output. Avoid readable text inside the illustration unless "
        "the instruction explicitly requests it.\n\n"
        f"Visual instruction: {instruction.strip()}"
    )
