"""Demo executável do fluxo de KYC (ver docs/kyc-fluxo-lgpd.md).

NÃO é para produção: usa provedores SIMULADOS e dados fictícios. Serve para
apresentar o fluxo ponta a ponta — consentimento, validação cadastral,
biometria, antifraude, decisão, revisão humana e trilha de auditoria.
"""

from .app import create_app

__all__ = ["create_app"]
