# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""A chat with a model the LLM Gateway serves, as the signed-in user.

The gateway answers OpenAI's chat completions at llm.<domain>; it takes the
user's Keycloak token and routes the model id to the pod that serves it.
"""

from typing import Any, Dict, List

import httpx

from app.core.config import settings

# A first answer can wait for a model that has just started
TIMEOUT = httpx.Timeout(300.0)


class GatewayError(Exception):
    """The gateway refused or failed the chat; `status` is its HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def chat(token: str, model: str, messages: List[Dict[str, str]], max_tokens: int, temperature: float) -> Dict[str, Any]:
    url = f"https://llm.{settings.DOMAIN_NAME}/v1/chat/completions"
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise GatewayError(502, f"the LLM Gateway did not answer: {e}")
    if r.status_code >= 400:
        try:
            error = r.json()
            detail = error.get("detail") or error.get("error", {}).get("message") or r.text
        except ValueError:
            detail = r.text
        raise GatewayError(r.status_code, f"the LLM Gateway answered {r.status_code} for {model}: {detail}")
    answer = r.json()
    return {"content": answer["choices"][0]["message"].get("content") or "", "usage": answer.get("usage")}
