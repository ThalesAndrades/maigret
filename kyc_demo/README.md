# Demo de KYC com Biometria (conforme LGPD)

Ferramenta **executável de apresentação** do fluxo descrito em
[`docs/kyc-fluxo-lgpd.md`](../docs/kyc-fluxo-lgpd.md). Mostra, ponta a ponta:
consentimento → validação cadastral → prova de vida + match facial →
antifraude → decisão → revisão humana, com trilha de auditoria encadeada.

> ⚠️ **Não é para produção.** Usa **provedores simulados** (mocks) e dados
> fictícios. Nenhuma API externa é chamada e nenhum dado real é tratado.

## Como rodar

```bash
pip install flask          # única dependência extra
python -m kyc_demo.app      # sobe em http://127.0.0.1:5005
# porta alternativa: PORT=8080 python -m kyc_demo.app
```

Abra o navegador em `http://127.0.0.1:5005` e use o formulário para simular
clientes. A página inicial mostra a **fila de revisão humana** e a **trilha de
auditoria** (com indicador de integridade da cadeia de hashes).

## Caminhos de demonstração

| Entrada | Resultado |
|---|---|
| CPF válido + selfie `selfie-ok` | **aprovado** |
| selfie `low-quality` | **em revisão** (similaridade limítrofe) |
| selfie `spoof-attack` | **recusado** (liveness falha) |
| nome contendo `DIVERGE` | **recusado** (divergência cadastral) |
| CPF inválido (ex.: `11111111111`) | **recusado** (cadastral) |

CPF válido de teste: `52998224725`.

## API REST (contrato que um app mobile consumiria)

```
POST /v1/kyc/sessions              -> inicia sessão
POST /v1/kyc/{sid}/consent         -> registra consentimento (cadastral+biometria)
POST /v1/kyc/{sid}/identity        -> nome, cpf, data_nascimento
POST /v1/kyc/{sid}/biometrics      -> refs de selfie/documento (efêmeras)
POST /v1/kyc/{sid}/submit          -> orquestra e decide
GET  /v1/kyc/{sid}                 -> status da sessão
GET  /v1/audit                     -> trilha + integridade da cadeia
```

A sessão é uma **máquina de estados**; chamar uma etapa fora de ordem retorna
`409`.

## O que o demo demonstra de conformidade

- **Consentimento separado** para dado comum e biometria (escopos distintos),
  append-only — revogar é inserir nova linha, nunca `UPDATE/DELETE`.
- **Confirmar sem reter**: persiste apenas vereditos, scores e referências;
  nunca a selfie, a imagem do documento ou o PII "verdadeiro".
- **Descarte da biometria** após a decisão (evento `biometrics_purged`).
- **Trilha de auditoria imutável** com hash encadeado — adulterar uma linha
  quebra a verificação (`GET /v1/audit` → `chain_ok: false`).
- **Revisão humana** (art. 20) para casos `em_revisao`.

## Arquivos

| Arquivo | Papel |
|---|---|
| `store.py` | SQLite: subject, consent_record, kyc_verification, audit_log (hash chain) |
| `providers.py` | Mocks de Datavalid, biometria e antifraude (validação real do CPF inclusa) |
| `engine.py` | Orquestração, máquina de estados e regras de decisão |
| `app.py` | Flask: UI de apresentação + API REST |
| `templates/` | Páginas da apresentação |

## Migração para produção

Troque os mocks de `providers.py` pelos SDKs reais (Serpro Datavalid,
Unico/idwall/CAF, ClearSale/Serasa), mova as imagens para storage cifrado
efêmero com URL pré-assinada, persista as sessões com TTL e siga o checklist
da seção 16 de `docs/kyc-fluxo-lgpd.md`.
