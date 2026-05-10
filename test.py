"""Standalone model connectivity test.

This script does not import any project code. It only uses environment
variables and LangChain's public model initializer to verify whether the model
endpoint can respond normally.

Supported environment variables:
    MODEL / OPENAI_MODEL / LOCAL_MODEL
    BASE_URL / OPENAI_BASE_URL / OPENAI_API_BASE / LOCAL_BASE_URL
    API_KEY / OPENAI_API_KEY / LOCAL_API_KEY

Usage:
    python test.py
    python test.py --message "hello"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model


DEFAULT_MESSAGE = "hello, please respond with exactly: model test ok"


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def resolve_model_config() -> tuple[str, str | None, str | None]:
    model_name = first_non_empty(
        os.environ.get("MODEL"),
    )
    base_url = first_non_empty(
        os.environ.get("OPENAI_API_BASE"),
    )
    api_key = first_non_empty(
        os.environ.get("OPENAI_API_KEY"),
    )

    if not model_name:
        raise ValueError(
            "No model configured. Set MODEL, OPENAI_MODEL, or LOCAL_MODEL."
        )

    if "/" not in model_name:
        raise ValueError(
            f"Model must be in provider/model form, got: {model_name!r}"
        )

    return model_name, base_url, api_key


async def run_test(message: str) -> int:
    model_name, base_url, api_key = resolve_model_config()
    provider, model = model_name.split("/", maxsplit=1)

    kwargs: dict[str, str] = {}
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key

    print("== Model Config ==")
    print(f"model: {model_name}")
    print(f"base_url: {base_url}")
    print(f"api_key_set: {bool(api_key)}")
    print(f"message: {message}")

    chat_model = init_chat_model(model, model_provider=provider, **kwargs)
    response = await chat_model.ainvoke([("human", message)])

    print("\n== Response ==")
    print(f"type: {type(response).__name__}")
    print("content:")
    print(getattr(response, "content", response))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone model connectivity test.")
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Message sent to the model.",
    )
    return parser


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    load_dotenv(override=True)

    try:
        return await run_test(args.message.strip())
    except Exception as exc:
        print("\n== Test Failed ==", file=sys.stderr)
        print(f"type: {type(exc).__name__}", file=sys.stderr)
        print(f"error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
