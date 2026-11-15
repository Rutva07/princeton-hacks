"""Interchangeable chat-model factory for LangGraph agent roles."""
from __future__ import annotations

from .config import settings


def _auto_provider():
    if settings.openai_api_key:return 'openai'
    if settings.openrouter_api_key:return 'openrouter'
    if settings.nvidia_api_key:return 'nvidia'
    if settings.groq_api_key:return 'groq'
    return None


def model_available(provider='auto'):
    provider=_auto_provider() if provider=='auto' else provider
    return provider in ('openai','openrouter','nvidia','groq','ollama') and (
        provider=='ollama' or bool({
            'openai':settings.openai_api_key,'openrouter':settings.openrouter_api_key,
            'nvidia':settings.nvidia_api_key,'groq':settings.groq_api_key}.get(provider)))


def get_model(role: str, temperature: float = 0):
    from langchain_openai import ChatOpenAI
    provider=getattr(settings,f'{role}_model_provider','auto')
    model_name=getattr(settings,f'{role}_model','')
    if provider=='auto':provider=_auto_provider()
    if not provider:raise RuntimeError(f'No model provider is configured for {role}')
    if provider=='openai':
        return ChatOpenAI(model=model_name or settings.openai_model,
            api_key=settings.openai_api_key,temperature=temperature,timeout=60,max_retries=2)
    if provider=='openrouter':
        return ChatOpenAI(model=model_name or settings.openrouter_model,
            api_key=settings.openrouter_api_key,base_url='https://openrouter.ai/api/v1',
            temperature=temperature,timeout=60,max_retries=2)
    if provider=='nvidia':
        return ChatOpenAI(model=model_name or 'meta/llama-3.1-70b-instruct',
            api_key=settings.nvidia_api_key,base_url=settings.nvidia_base_url,
            temperature=temperature,timeout=60,max_retries=2)
    if provider=='groq':
        return ChatOpenAI(model=model_name or 'llama-3.3-70b-versatile',
            api_key=settings.groq_api_key,base_url=settings.groq_base_url,
            temperature=temperature,timeout=60,max_retries=2)
    if provider=='ollama':
        return ChatOpenAI(model=model_name or 'qwen2.5-coder:7b',api_key='ollama',
            base_url=settings.ollama_base_url,temperature=temperature,timeout=120,max_retries=1)
    raise RuntimeError(f'Unsupported model provider: {provider}')

