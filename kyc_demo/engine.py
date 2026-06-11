"""Orquestração do KYC: máquina de estados da sessão + decisão.

Reproduz o §4 e §10 do design: consentimento -> validação cadastral ->
liveness/match facial -> antifraude -> decisão (aprovado/em_revisao/recusado).
"""

from dataclasses import dataclass, field

from . import store
from .providers import MockDatavalid, MockBiometria, MockAntifraude

LIMIAR_FACIAL = 0.85
LIMIAR_RISCO = 550

# Estados da sessão (apresentados na UI)
STATES = [
    "created",
    "consent_given",
    "identity_provided",
    "biometrics_provided",
    "processing",
    "decided",
]

# sessões em memória (demo). Em produção: persistidas + TTL curto.
_sessions: dict[str, "Session"] = {}


@dataclass
class Session:
    id: str
    subject_id: str
    state: str = "created"
    identity: dict = field(default_factory=dict)      # nome/cpf/nascimento (efêmero)
    biometrics: dict = field(default_factory=dict)    # refs efêmeras
    verification_id: str | None = None
    result: dict | None = None


class FlowError(Exception):
    pass


def new_session(external_ref=None) -> Session:
    subject_id = store.create_subject(external_ref)
    sid = "sess-" + subject_id[:12]
    s = Session(id=sid, subject_id=subject_id)
    _sessions[sid] = s
    store.audit("kyc_session_created", subject_id, {"session": sid})
    return s


def get_session(sid) -> Session:
    s = _sessions.get(sid)
    if not s:
        raise FlowError("sessão inexistente")
    return s


def give_consent(sid, scopes, policy_version, terms_version, meta=None) -> str:
    s = get_session(sid)
    if s.state != "created":
        raise FlowError(f"consentimento não permitido no estado '{s.state}'")
    meta = meta or {}
    ref = None
    for scope in scopes:
        basis = "consentimento" if scope == "biometria" else "contrato"
        ref = store.record_consent(
            s.subject_id, scope, True, policy_version, terms_version, basis,
            ip=meta.get("ip"), device_id=meta.get("device_id"),
            user_agent=meta.get("user_agent"),
        )
    store.audit("consent_granted", s.subject_id,
                {"scopes": scopes, "policy": policy_version})
    s.state = "consent_given"
    return ref


def provide_identity(sid, nome, cpf, nascimento):
    s = get_session(sid)
    if s.state != "consent_given":
        raise FlowError(f"identidade não permitida no estado '{s.state}'")
    s.identity = {"nome": nome, "cpf": cpf, "nascimento": nascimento}
    store.audit("identity_provided", s.subject_id, {"cpf_len": len(cpf or "")})
    s.state = "identity_provided"


def provide_biometrics(sid, selfie_ref, documento_ref):
    s = get_session(sid)
    if s.state != "identity_provided":
        raise FlowError(f"biometria não permitida no estado '{s.state}'")
    s.biometrics = {"selfie_ref": selfie_ref, "documento_ref": documento_ref}
    store.audit("biometrics_provided", s.subject_id, {})
    s.state = "biometrics_provided"


def submit(sid) -> dict:
    s = get_session(sid)
    if s.state != "biometrics_provided":
        raise FlowError(f"submissão não permitida no estado '{s.state}'")

    # Pré-condição: consentimento válido (comum + biometria)
    if not store.consent_is_valid(s.subject_id, ["cadastral", "biometria"]):
        raise FlowError("consentimento ausente/revogado")

    s.state = "processing"
    store.audit("kyc_start", s.subject_id, {"session": s.id})

    datavalid, biometria, antifraude = MockDatavalid(), MockBiometria(), MockAntifraude()
    steps = []

    # 1. validação cadastral
    cad = datavalid.validar(**{
        "nome": s.identity["nome"],
        "cpf": s.identity["cpf"],
        "nascimento": s.identity["nascimento"],
    })
    steps.append(("Validação cadastral (Datavalid)",
                  "OK" if cad.confere else "FALHA",
                  f"cpf_disponivel={cad.cpf_disponivel}, situacao={cad.situacao}, "
                  f"nome={cad.nome_ok}, nascimento={cad.nascimento_ok}"))
    if not cad.confere:
        return _finish(s, "recusado",
                       "dados cadastrais não conferem", cad, None, None, steps)

    # 2. liveness + match facial
    bio = biometria.verificar(**s.biometrics)
    steps.append(("Prova de vida + match facial",
                  "OK" if bio.liveness_ok and bio.match else
                  ("LIMÍTROFE" if bio.liveness_ok else "FALHA"),
                  f"liveness={bio.liveness_ok}, similaridade={bio.similaridade}"))
    if not bio.liveness_ok:
        return _finish(s, "recusado", "prova de vida falhou", cad, bio, None, steps)
    if bio.similaridade < LIMIAR_FACIAL:
        return _finish(s, "em_revisao",
                       "similaridade facial limítrofe", cad, bio, None, steps)

    # 3. antifraude / listas
    risco = antifraude.score(cpf=s.identity["cpf"])
    steps.append(("Antifraude / listas restritivas",
                  "REVISÃO" if (risco.flag_pep or risco.flag_sancoes) else
                  ("OK" if risco.score >= LIMIAR_RISCO else "BAIXO"),
                  f"score={risco.score}, pep={risco.flag_pep}, "
                  f"sancoes={risco.flag_sancoes}"))
    if risco.flag_pep or risco.flag_sancoes:
        return _finish(s, "em_revisao", "lista restritiva", cad, bio, risco, steps)
    if risco.score < LIMIAR_RISCO:
        return _finish(s, "recusado", "score de risco baixo", cad, bio, risco, steps)

    return _finish(s, "aprovado", "", cad, bio, risco, steps)


def _finish(s, status, reason, cad, bio, risco, steps) -> dict:
    consent_ref = store.current_consent_ref(s.subject_id, "biometria")
    provider_refs = {}
    if cad:
        provider_refs["datavalid"] = cad.provider_ref
    if bio:
        provider_refs["biometria"] = bio.provider_ref
    if risco:
        provider_refs["antifraude"] = risco.provider_ref
    risk_flags = ({"score": risco.score, "pep": risco.flag_pep,
                   "sancoes": risco.flag_sancoes} if risco else {})

    vid = store.record_verification(
        subject_id=s.subject_id, status=status, reason=reason, decided_by="auto",
        cadastral_ok=(cad.confere if cad else None),
        liveness_ok=(bio.liveness_ok if bio else None),
        face_similarity=(bio.similaridade if bio else None),
        risk_flags=risk_flags, provider_refs=provider_refs, consent_ref=consent_ref,
    )
    store.audit("kyc_decision", s.subject_id,
                {"status": status, "reason": reason, "verification_id": vid})

    # Aprovou/decidiu -> agenda purge da biometria efêmera (aqui: descarta já)
    s.biometrics = {"purged": True}
    store.audit("biometrics_purged", s.subject_id, {"verification_id": vid})

    s.state = "decided"
    s.verification_id = vid
    s.result = {
        "status": status, "reason": reason, "verification_id": vid,
        "review_required": status != "aprovado", "steps": steps,
    }
    return s.result


def human_decision(verification_id, status, analyst_id, reason=""):
    """Revisão humana de um caso 'em_revisao' (art. 20 LGPD)."""
    v = store.get_verification(verification_id)
    if not v:
        raise FlowError("verificação inexistente")
    store.update_verification_decision(verification_id, status, reason, analyst_id)
    store.audit("human_review", v["subject_id"],
                {"verification_id": verification_id, "status": status,
                 "analyst": analyst_id, "reason": reason})
    return {"verification_id": verification_id, "status": status,
            "decided_by": analyst_id}
