import type { Path, Schema, SchemaTools } from "./schema";

export const ENUM_LABEL: Record<string, string> = {
  electronics: "Electronics",
  mechanical: "Mechanical",
  power_bank: "Power bank",
  ceiling_fan: "Ceiling fan",
  generic_electronics: "Other electronics",
  generic_mechanical: "Other mechanical",
  greenfield: "New building (greenfield)",
  existing_shed: "Existing shed",
  basic: "Basic",
  standard: "Standard",
  premium: "Premium",
  manual: "Mostly manual",
  semi: "Semi-automatic",
  high: "Highly automated",
  KA: "Karnataka",
  N: "North",
  E: "East",
  S: "South",
  W: "West",
  dock: "Dock",
  personnel: "Personnel",
  roller_shutter: "Roller shutter",
  emergency: "Emergency exit",
};

/** A fresh value for a schema node: defaults, required objects filled, arrays empty. */
export function defaultFor(tools: SchemaTools, path: Path): unknown {
  const { schema } = tools.at(path);
  return build(tools, schema);
}

function build(tools: SchemaTools, s: Schema): unknown {
  if (s.default !== undefined) return structuredClone(s.default);
  const t = s.type;
  if (t === "object") {
    const out: Record<string, unknown> = {};
    const props = (s.properties ?? {}) as Record<string, Schema>;
    for (const [k, sub] of Object.entries(props)) {
      const u = tools.unwrap(sub);
      out[k] = u.nullable && u.schema.default === undefined ? null : build(tools, u.schema);
    }
    return out;
  }
  if (t === "array") {
    if (Array.isArray(s.prefixItems)) return (s.prefixItems as Schema[]).map(() => null);
    return [];
  }
  if (s.const !== undefined) return s.const;
  if (Array.isArray(s.enum)) return (s.enum as unknown[])[0] ?? null;
  if (t === "string") return "";
  return null;
}
