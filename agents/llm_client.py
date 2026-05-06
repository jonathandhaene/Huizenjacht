"""
Shared LLM client factory.

Returns an OpenAI-compatible client preferring (in order):
  1. OpenAI proper, when ``OPENAI_API_KEY`` is configured.
  2. GitHub Models (https://models.github.ai/inference), when ``GITHUB_TOKEN``
     is configured. This is free for GitHub users and exposes the same
     ``openai`` SDK surface — only the ``base_url`` and model id differ.
  3. ``None`` when neither credential is available; callers should then fall
     back to a rule-based code path.

The factory also returns the model id that should be used with the chosen
backend so callers don't have to special-case the publisher/name format
required by GitHub Models (e.g. ``openai/gpt-4o-mini``).
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)


def get_chat_client(prefer_cheap: bool = False) -> Tuple[Optional[object], str, str]:
    """Build a chat client.

    Parameters
    ----------
    prefer_cheap:
        When True and OpenAI is selected, downgrade ``gpt-4o`` to
        ``gpt-4o-mini`` to keep extraction calls inexpensive.

    Returns
    -------
    (client, model, backend)
        ``client`` is an ``openai.OpenAI`` instance or ``None``.
        ``backend`` is one of ``"openai"``, ``"github"``, ``"none"``.
    """
    try:
        import openai  # lazy import — only required when a backend is configured
    except ImportError:
        logger.warning("[llm_client] openai package not installed")
        return None, "", "none"

    if settings.openai_api_key:
        model = settings.openai_model or "gpt-4o"
        if prefer_cheap and model == "gpt-4o":
            model = "gpt-4o-mini"
        return openai.OpenAI(api_key=settings.openai_api_key), model, "openai"

    if settings.github_token:
        client = openai.OpenAI(
            api_key=settings.github_token,
            base_url=settings.github_models_base_url,
        )
        return client, settings.github_models_model, "github"

    return None, "", "none"
