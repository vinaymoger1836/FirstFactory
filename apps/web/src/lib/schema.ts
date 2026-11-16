/**
 * Small JSON-Schema helpers for the wizard (no runtime schema compiler: the strict CSP has
 * no 'unsafe-eval'). The server re-validates with Pydantic and returns exact paths.
 */
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Schema = Record<string, unknown>;
export type Path = (string | number)[];

export class SchemaTools {
  constructor(private readonly root: Schema) {}

  private get defs(): Record<string, Schema> {
    return (this.root.$defs ?? {}) as Record<string, Schema>;
  }

  resolve(node: Schema | undefined): Schema {
    let n = node ?? {};
    for (let i = 0; i < 10 && typeof n.$ref === "string"; i++) {
      const name = (n.$ref as string).split("/").pop() ?? "";
      n = this.defs[name] ?? {};
    }
    return n;
  }

  /** Strip `anyOf: [X, {type: null}]` and report whether null is allowed. */
  unwrap(node: Schema | undefined): { schema: Schema; nullable: boolean } {
    const n = this.resolve(node);
    const any = n.anyOf as Schema[] | undefined;
    if (Array.isArray(any)) {
      const nonNull = any.filter((s) => (s as Schema).type !== "null");
      if (nonNull.length === 1) {
        return { schema: { ...n, ...this.resolve(nonNull[0]), anyOf: undefined }, nullable: true };
      }
    }
    return { schema: n, nullable: false };
  }

  rootSchema(): Schema {
    return this.resolve(this.root);
  }

  /** Sub-schema at a property path (array indexes step into `items`). */
  at(path: Path): { schema: Schema; nullable: boolean; required: boolean } {
    let cur = this.rootSchema();
    let nullable = false;
    let required = true;
    for (const key of path) {
      const { schema } = this.unwrap(cur);
      if (typeof key === "number") {
        const prefix = schema.prefixItems as Schema[] | undefined;
        cur = prefix ? (prefix[key] ?? {}) : ((schema.items as Schema | undefined) ?? {});
        required = true;
      } else {
        const props = (schema.properties ?? {}) as Record<string, Schema>;
        required = ((schema.required as string[] | undefined) ?? []).includes(key);
        cur = props[key] ?? {};
      }
      const u = this.unwrap(cur);
      cur = u.schema;
      nullable = u.nullable;
    }
    return { schema: cur, nullable, required };
  }
}

export function getAt(value: unknown, path: Path): unknown {
  let cur: unknown = value;
  for (const k of path) {
    if (cur === null || cur === undefined) return undefined;
    cur = (cur as Record<string | number, unknown>)[k];
  }
  return cur;
}

export function setAt<T>(value: T, path: Path, next: unknown): T {
  if (!path.length) return next as T;
  const [head, ...rest] = path;
  const base: unknown = value ?? (typeof head === "number" ? [] : {});
  if (Array.isArray(base)) {
    const copy = [...(base as unknown[])];
    copy[head as number] = setAt(copy[head as number], rest, next);
    return copy as T;
  }
  const obj = { ...(base as Record<string, unknown>) };
  obj[head as string] = setAt(obj[head as string], rest, next);
  return obj as T;
}

export function pathKey(path: Path): string {
  return path.join(".");
}

const WORD_FIX: Record<string, string> = {
  inr: "(₹)",
  pct: "(%)",
  m: "(m)",
  mm: "(mm)",
  oee: "OEE",
  sqm: "per m²",
  bom: "BoM",
};

/** "annual_volume" → "Annual volume"; "budget_inr" → "Budget (₹)". */
export function humanize(key: string): string {
  const words = key.split("_").map((w) => WORD_FIX[w] ?? w);
  const s = words.join(" ").replace(/\s+\(/g, " (");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** One-field check against the schema (required, type, range, length, pattern). */
export function checkValue(schema: Schema, value: unknown, required: boolean, nullable: boolean): string | null {
  const empty = value === null || value === undefined || value === "";
  if (empty) return required && !nullable && schema.default === undefined ? "This is required." : null;
  const type = schema.type;
  if (type === "number" || type === "integer") {
    if (typeof value !== "number" || Number.isNaN(value)) return "Enter a number.";
    if (type === "integer" && !Number.isInteger(value)) return "Enter a whole number.";
    if (typeof schema.minimum === "number" && value < schema.minimum) return `At least ${schema.minimum}.`;
    if (typeof schema.exclusiveMinimum === "number" && value <= schema.exclusiveMinimum)
      return `More than ${schema.exclusiveMinimum}.`;
    if (typeof schema.maximum === "number" && value > schema.maximum) return `At most ${schema.maximum}.`;
  }
  if (type === "string" && typeof value === "string") {
    if (typeof schema.minLength === "number" && value.trim().length < schema.minLength)
      return schema.minLength <= 1 ? "This is required." : `At least ${schema.minLength} characters.`;
    if (typeof schema.maxLength === "number" && value.length > schema.maxLength)
      return `At most ${schema.maxLength} characters.`;
    if (typeof schema.pattern === "string" && !new RegExp(schema.pattern).test(value))
      return "Use letters, digits, '-', '_' or '.' (no spaces).";
    const en = schema.enum as unknown[] | undefined;
    if (en && !en.includes(value)) return "Pick one of the options.";
  }
  return null;
}
