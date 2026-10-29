import { createLibrary, defineComponent, defineFunction, type DefinedComponent } from "@openuidev/react-lang";
import { z } from "zod/v4";
import { gridSchema, taskDeclarationSchema } from "../runtime/schemas";
import { PendingRuntimeView, SlotRuntimeProvider, WidgetRuntimeBridge } from "../runtime/widget";

export interface WidgetModule { widget: DefinedComponent<any>; interactionActions: string[] }
export type WidgetModules = Map<string, WidgetModule>;

/** Build the exact custom library consumed by OpenUI's native Renderer. */
export function createLumiLibrary(modules: WidgetModules) {
  if (!modules.size) throw new Error("Lumi library requires at least one widget");
  const widgets = [...modules.values()].map(({ widget }) => defineComponent({
    name: widget.name,
    description: widget.description,
    props: widget.props,
    component: args => <WidgetRuntimeBridge type={widget.name} props={args.props}>
      <widget.component {...args}/>
    </WidgetRuntimeBridge>,
  }));
  const widgetIds = [...modules.keys()];
  let pendingRef: z.core.$ZodType = z.never();
  const contentRef = () => z.union([...widgets.map(widget => widget.ref), pendingRef]);
  const Pending = defineComponent({
    name: "Pending",
    description: "Widget chưa đủ dữ liệu; Python kiểm tra skeleton/children và cấp anchor.",
    props: z.strictObject({
      widget_id: z.enum(widgetIds as [string, ...string[]]),
      props: z.record(z.string(), z.unknown()),
      children: z.array(z.lazy(contentRef)).optional(),
    }),
    component: ({ props }) => <PendingRuntimeView widgetId={props.widget_id}/>,
  });
  pendingRef = Pending.ref;
  const Slot = defineComponent({
    name: "Slot",
    description: "Root block giữ layout và metadata/runtime state hiện có của Lumi.",
    props: z.strictObject({
      grid: gridSchema,
      content: contentRef(),
      slot_role: z.string().nullish(),
      description: z.string().nullish(),
      initial_visibility: z.enum(["visible", "hidden"]).nullish(),
      initial_state: z.record(z.string(), z.union([z.string(), z.boolean()])).nullish(),
    }),
    component: ({ props, renderNode, statementId }) => statementId ? <SlotRuntimeProvider
      statementId={statementId}
      grid={props.grid}
      description={props.description}
      initialState={{ ...(props.initial_state || {}), visibility: props.initial_visibility || "visible" }}
    >{renderNode(props.content)}</SlotRuntimeProvider> : null,
  });
  const SurfaceProgram = defineComponent({
    name: "SurfaceProgram",
    description: "Surface Lumi gồm các Slot; task được Python điều phối.",
    props: z.strictObject({
      blocks: z.array(Slot.ref).min(1),
      tasks: z.array(taskDeclarationSchema).optional(),
      template_description: z.string().nullish(),
    }),
    component: ({ props, renderNode }) => <main className="surface-document-grid" aria-label="Nội dung trực quan">{renderNode(props.blocks)}</main>,
  });
  const asyncTask = defineFunction({
    name: "async_task",
    description: "Khai báo task thuần túy; chỉ Python kiểm tra và lập lịch executor.",
    params: taskDeclarationSchema,
    returns: taskDeclarationSchema,
    fn: task => task,
  });
  return createLibrary({ root: "SurfaceProgram", components: [SurfaceProgram, Slot, Pending, ...widgets], functions: [asyncTask] });
}

export async function loadWidgetCatalog(entries: any[]): Promise<WidgetModules> {
  const modules: WidgetModules = new Map();
  await Promise.all(entries.map(async entry => {
    if (!/^[a-z]+(?:_[a-z]+)*$/.test(entry.id) || !entry.renderer?.startsWith("/assets/") || !Array.isArray(entry.interaction_actions)) throw new Error("Invalid widget catalog");
    const module: WidgetModule = await import(/* @vite-ignore */ entry.renderer);
    if (!module.widget?.component || !module.widget?.props || JSON.stringify(module.interactionActions) !== JSON.stringify(entry.interaction_actions)) throw new Error(`Invalid OpenUI widget ${entry.id}`);
    modules.set(entry.id, module);
  }));
  return new Map([...modules].sort(([a], [b]) => a.localeCompare(b)));
}
