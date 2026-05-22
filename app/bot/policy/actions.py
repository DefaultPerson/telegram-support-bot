from __future__ import annotations

from .context import EvalContext
from .decision import Decision
from .schema import Action, PolicyDocument


def render_template(doc: PolicyDocument, key: str, language: str) -> str:
    """
    Render a template by key in the requested language.

    Falls back to ``defaults.language_fallback`` and then to any available
    translation. Substitutes ``variables`` via ``str.format_map`` — a missing
    variable raises KeyError, surfacing config mistakes early.
    """
    template = doc.templates.get(key)
    if template is None:
        raise KeyError(f"Template {key!r} not found in policy templates")

    lang = language if language in template else doc.defaults.language_fallback
    text = template.get(lang)
    if text is None:
        text = next(iter(template.values()))

    return text.format_map(doc.variables)


def apply_action(action: Action, ctx: EvalContext, doc: PolicyDocument, decision: Decision) -> None:
    """Mutate ``decision`` according to a single matched action."""
    if action.type == "suppress_topic_creation":
        decision.suppress_topic_creation = True
    elif action.type == "suppress_group_notify":
        decision.suppress_group_notify = True
    elif action.type == "close_topic":
        decision.close_topic = True
    elif action.type == "escalate":
        decision.escalate = True
    elif action.type == "auto_reply":
        if not action.template_key:
            raise ValueError("auto_reply action requires 'template_key'")
        decision.auto_replies.append(
            render_template(doc, action.template_key, ctx.language)
        )
