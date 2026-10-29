import { z } from "zod/v4";
export const text = () => z.string().trim().min(1).meta({ template_value_kind: "binding", lumi_trim: true });
// XOR represented in JSON Schema too; no refinement that Python cannot execute.
export const mediaFields = {
  asset_id: text().nullish().describe("ID ảnh từ Asset Catalog.").meta({ source: "asset_catalog.id", template_value_kind: "binding" }),
  media_id: text().nullish().describe("ID media đã được backend xác minh trong phiên hiện tại.").meta({ source: "knowledge.media_id", template_value_kind: "binding" }),
};
export const mediaChoice = {
  anyOf: [
    { required: ["asset_id"], properties: { asset_id: { type: "string" }, media_id: { type: "null" } } },
    { required: ["media_id"], properties: { media_id: { type: "string" }, asset_id: { type: "null" } } },
  ],
};
export function mediaProps<T extends z.ZodRawShape>(shape: T) {
  return z.strictObject({ ...mediaFields, ...shape }).refine(
    value => ((value as Record<string, unknown>).asset_id != null) !== ((value as Record<string, unknown>).media_id != null),
    "Exactly one of asset_id and media_id is required",
  ).meta({ ...mediaChoice, exactly_one_of: ["asset_id", "media_id"], template_value_kind: "binding" });
}
export const gridSchema = z.strictObject({
  col: z.number().int().min(1).max(16), row: z.number().int().min(1).max(10),
  col_span: z.number().int().min(1).max(16), row_span: z.number().int().min(1).max(10),
});
export const taskDeclarationSchema = z.strictObject({
  evidence_ref: text(), task_type: text(), target_slots: z.array(text()).min(1), instruction: text(),
});
