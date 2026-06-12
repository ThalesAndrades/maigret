"""App Flask do demo de KYC.

Dois públicos:
- UI de apresentação em "/" (passo a passo do fluxo, fila de revisão, auditoria).
- API REST em /v1/kyc/... (o contrato que um app mobile consumiria).
"""

import os
from flask import Flask, render_template, request, jsonify, redirect, url_for

from . import store, engine

POLICY_VERSION = "2026-05"
TERMS_VERSION = "2026-05"


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("KYC_DEMO_SECRET", os.urandom(16).hex())
    store.init_db()

    def _meta():
        return {
            "ip": request.remote_addr,
            "device_id": request.headers.get("X-Device-Id", "demo-device"),
            "user_agent": request.headers.get("User-Agent", ""),
        }

    # ----------------------------- UI ------------------------------------ #
    @app.get("/")
    def index():
        return render_template(
            "index.html",
            states=engine.STATES,
            reviews=store.list_reviews(),
            audit=store.audit_trail(30),
            chain_ok=store.verify_audit_chain(),
            policy=POLICY_VERSION,
            terms=TERMS_VERSION,
        )

    @app.post("/run")
    def run_demo():
        """Executa o fluxo inteiro de uma vez, a partir do formulário da UI."""
        f = request.form
        s = engine.new_session(external_ref=f.get("nome") or None)
        engine.give_consent(
            s.id, ["cadastral", "biometria"], POLICY_VERSION, TERMS_VERSION, _meta()
        )
        engine.provide_identity(
            s.id, f.get("nome", ""), f.get("cpf", ""), f.get("nascimento", "")
        )
        engine.provide_biometrics(
            s.id, f.get("selfie_ref", "selfie-ok"), "doc-ref"
        )
        result = engine.submit(s.id)
        return render_template(
            "result.html", session_id=s.id, subject=s.subject_id, result=result,
        )

    @app.post("/review/<verification_id>")
    def review(verification_id):
        decision = request.form.get("decision", "aprovado")
        engine.human_decision(
            verification_id, decision, analyst_id="analista-demo",
            reason=request.form.get("reason", "revisão manual"),
        )
        return redirect(url_for("index"))

    @app.post("/reset")
    def reset():
        store.reset_db()
        engine._sessions.clear()
        return redirect(url_for("index"))

    # ----------------------------- API ----------------------------------- #
    @app.post("/v1/kyc/sessions")
    def api_session():
        s = engine.new_session()
        return jsonify({"session_id": s.id, "subject_id": s.subject_id,
                        "state": s.state})

    @app.post("/v1/kyc/<sid>/consent")
    def api_consent(sid):
        body = request.get_json(force=True, silent=True) or {}
        ref = engine.give_consent(
            sid, body.get("scopes", ["cadastral", "biometria"]),
            body.get("policy_version", POLICY_VERSION),
            body.get("terms_version", TERMS_VERSION), _meta(),
        )
        return jsonify({"status": "consent_given", "consent_ref": ref})

    @app.post("/v1/kyc/<sid>/identity")
    def api_identity(sid):
        b = request.get_json(force=True, silent=True) or {}
        engine.provide_identity(sid, b.get("nome"), b.get("cpf"),
                                b.get("data_nascimento"))
        return jsonify({"status": "identity_provided"})

    @app.post("/v1/kyc/<sid>/biometrics")
    def api_biometrics(sid):
        b = request.get_json(force=True, silent=True) or {}
        engine.provide_biometrics(sid, b.get("selfie_ref"),
                                  b.get("documento_ref"))
        return jsonify({"status": "biometrics_provided"})

    @app.post("/v1/kyc/<sid>/submit")
    def api_submit(sid):
        return jsonify(engine.submit(sid))

    @app.get("/v1/kyc/<sid>")
    def api_status(sid):
        s = engine.get_session(sid)
        return jsonify({"session_id": s.id, "state": s.state, "result": s.result})

    @app.get("/v1/audit")
    def api_audit():
        return jsonify({"chain_ok": store.verify_audit_chain(),
                        "trail": store.audit_trail()})

    @app.errorhandler(engine.FlowError)
    def _flow_error(e):
        return jsonify({"error": str(e)}), 409

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5005"))
    app.run(host="0.0.0.0", port=port, debug=False)
