"""Intake prompts. Kept small: the model gets text and a closed parameter list, never data
it does not need (docs/02 §4, docs/11 §2 privacy). Deterministic: no IDs or dates."""

from __future__ import annotations

from typing import Any

from fl_domain.library.catalog import ParamSpec

__all__ = ["doc_messages", "param_table", "summary_messages", "text_messages", "vision_messages"]

ARCHETYPES = "power_bank, ceiling_fan, generic_electronics, generic_mechanical"


def param_table(params: list[ParamSpec]) -> str:
    return "\n".join(f"- {p.name} | {p.unit} | {p.description}" for p in params)


_RULES = """Rules:
- Use only values the text states. Do not guess or calculate. Skip parameters it does not state.
- "evidence" must copy the exact words from the text that state the value.
- "variant" is the variant code the value applies to, or null when it applies to every variant.
- Give "value" as a plain number in the unit listed for that parameter."""


def text_messages(
    product_text: str, variants: list[tuple[str, str]], params: list[ParamSpec]
) -> list[dict[str, Any]]:
    vlist = "\n".join(f"- {code}: {name}" for code, name in variants)
    return [
        {
            "role": "user",
            "content": (
                "Extract product parameters from the product description.\n\n"
                f"Allowed parameters (name | unit | meaning):\n{param_table(params)}\n\n"
                f"Variants:\n{vlist}\n\n{_RULES}\n"
                f'- "archetype_guess" is one of: {ARCHETYPES}, or null.\n'
                '- "keywords": up to 10 words that name the product type and main parts.\n\n'
                f"TEXT:\n{product_text}"
            ),
        }
    ]


def doc_messages(
    pages: list[tuple[int, str]], variants: list[tuple[str, str]], params: list[ParamSpec]
) -> list[dict[str, Any]]:
    vlist = "\n".join(f"- {code}: {name}" for code, name in variants)
    body = "\n\n".join(f"[page {n}]\n{t}" for n, t in pages)
    return [
        {
            "role": "user",
            "content": (
                "Extract product parameters from this datasheet.\n\n"
                f"Allowed parameters (name | unit | meaning):\n{param_table(params)}\n\n"
                f"Variants:\n{vlist}\n\n{_RULES}\n"
                '- "page" is the page number where the value appears.\n'
                '- "other_specs": up to 15 further specifications with name, value, unit, page.\n\n'
                f"DATASHEET:\n{body}"
            ),
        }
    ]


def vision_messages(data_url: str, product_name: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f'This photo shows the product "{product_name}". List the visible '
                        "components, visible features (ports, LEDs, buttons, blades, canopy and "
                        "so on) and any size cues. Do not guess hidden parts. "
                        f'"archetype_guess" is one of: {ARCHETYPES}, or null.'
                    ),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]


def summary_messages(facts: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "Write a short summary for a first-time factory founder of what intake found "
                "about their product. Plain words, no numbers that are not in the facts, no "
                "advice beyond the open questions. Highlights: up to 5 short items.\n\n"
                f"FACTS:\n{facts}"
            ),
        }
    ]
