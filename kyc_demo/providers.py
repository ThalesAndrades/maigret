"""Provedores SIMULADOS para o demo de KYC.

Os provedores reais (Serpro Datavalid para validação cadastral; Unico/idwall/
CAF para biometria; ClearSale/Serasa para antifraude) exigem contrato,
credenciais e DPA. Aqui usamos mocks determinísticos para apresentar o fluxo
ponta a ponta sem tocar em dados reais nem em APIs externas.

A interface destes mocks espelha a do design: respondem com VEREDITOS
(bate/não bate, score), nunca devolvem o dado verdadeiro.
"""

import hashlib
import re
from dataclasses import dataclass


@dataclass
class CadastralResult:
    confere: bool
    cpf_disponivel: bool
    situacao: str
    nome_ok: bool
    nascimento_ok: bool
    provider_ref: str


@dataclass
class BiometriaResult:
    liveness_ok: bool
    match: bool
    similaridade: float
    provider_ref: str


@dataclass
class RiscoResult:
    score: int
    flag_pep: bool
    flag_sancoes: bool
    provider_ref: str


def _cpf_digits(cpf: str) -> str:
    return re.sub(r"\D", "", cpf or "")


def cpf_valido(cpf: str) -> bool:
    """Validação real dos dígitos verificadores do CPF (algoritmo público,
    não consulta nada)."""
    c = _cpf_digits(cpf)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for i in (9, 10):
        s = sum(int(c[j]) * ((i + 1) - j) for j in range(i))
        d = (s * 10) % 11 % 10
        if d != int(c[i]):
            return False
    return True


class MockDatavalid:
    """Simula a validação cadastral PF do Datavalid.

    Regra do demo (determinística e sem dados reais):
    - CPF precisa ter dígitos verificadores válidos -> cpf_disponivel;
      CPF inválido demonstra o caminho de recusa no cadastral;
    - nome e nascimento "conferem" se ambos forem preenchidos e o nome NÃO
      contiver o marcador 'DIVERGE' (atalho para demonstrar divergência).
    """

    def validar(self, *, nome: str, cpf: str, nascimento: str) -> CadastralResult:
        c = _cpf_digits(cpf)
        disponivel = cpf_valido(cpf)
        ref = "datavalid-" + hashlib.sha1(c.encode()).hexdigest()[:10]
        if not disponivel:
            return CadastralResult(False, False, "IRREGULAR", False, False, ref)
        bate = bool(nome) and bool(nascimento) and "DIVERGE" not in nome.upper()
        return CadastralResult(
            confere=bate,
            cpf_disponivel=True,
            situacao="REGULAR",
            nome_ok=bate,
            nascimento_ok=bate,
            provider_ref=ref,
        )


class MockBiometria:
    """Simula liveness + match facial.

    A similaridade é derivada do conteúdo da selfie (determinístico). Tokens
    de selfie começando com 'spoof' falham o liveness; com 'low' caem em
    similaridade limítrofe (para demonstrar o estado de revisão).
    """

    def verificar(self, *, selfie_ref: str, documento_ref: str) -> BiometriaResult:
        ref = "bio-" + hashlib.sha1((selfie_ref or "").encode()).hexdigest()[:10]
        token = (selfie_ref or "").lower()
        if token.startswith("spoof"):
            return BiometriaResult(False, False, 0.10, ref)
        if token.startswith("low"):
            return BiometriaResult(True, False, 0.82, ref)
        # similaridade pseudo-aleatória estável entre 0.88 e 0.99
        h = int(hashlib.sha1((selfie_ref or "x").encode()).hexdigest(), 16)
        sim = 0.88 + (h % 12) / 100.0
        return BiometriaResult(True, sim >= 0.85, round(sim, 3), ref)


class MockAntifraude:
    """Simula score antifraude e listas restritivas (PEP/sanções)."""

    def score(self, *, cpf: str) -> RiscoResult:
        c = _cpf_digits(cpf)
        ref = "fraud-" + hashlib.sha1(c.encode()).hexdigest()[:10]
        h = int(hashlib.sha1(c.encode()).hexdigest(), 16) if c else 0
        score = 500 + (h % 500)               # 500..999
        flag_pep = (h % 97) == 0              # raro
        flag_sancoes = (h % 197) == 0         # ainda mais raro
        return RiscoResult(score, flag_pep, flag_sancoes, ref)
