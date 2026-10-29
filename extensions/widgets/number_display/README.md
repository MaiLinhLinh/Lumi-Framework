# number_display

Renders a short, visually prominent numeric value. Its CSS is constrained for the 16 x 10 Surface grid.

`NumberDisplay(value)` initializes runtime `state.value` from the value prop.
After creation, the renderer and stage map use the current runtime value.
Gemini can update `value` (a string of at most 20 characters) and `visibility`
through `update_surface_state`, without planning or assembly. A planner patch
that explicitly supplies new number content or `initial_state.value` updates the
current value; unrelated edits and assembly completion preserve it.
