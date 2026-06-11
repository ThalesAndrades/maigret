# CLAUDE.md

Guia de contexto para trabalhar neste repositório com o Claude Code.

## O que é o Maigret

Maigret é uma ferramenta de OSINT que monta um dossiê sobre uma pessoa **a
partir apenas de um nome de usuário**, verificando a existência de contas em
milhares de sites e extraindo as informações públicas disponíveis nas páginas
de perfil. Não requer chaves de API. É um fork orientado a pesquisa do projeto
Sherlock, com uma base de dados de sites muito maior e extração de dados.

- Linguagem: Python 3.10+ (assíncrono, baseado em `asyncio`/`aiohttp`).
- Licença: MIT. Versão atual: ver `maigret/__version__.py`.
- Base de dados: ~3.150 sites e 14 "engines" em `maigret/resources/data.json`.

## Como executar

```bash
pip install -e .            # instala em modo desenvolvimento
python -m maigret USERNAME  # ou: maigret USERNAME (entrypoint do console)
```

- Por padrão verifica os 500 sites mais bem ranqueados por tráfego; `-a` varre
  todos, `--tags` filtra por categoria/país.
- Interface web (Flask): `maigret --web PORT` (ver `maigret/web/app.py`).
- Análise por IA opcional: flag `--ai` (usa API compatível com OpenAI).

## Arquitetura e fluxo de dados

O caminho principal é: **CLI → base de dados de sites → checagem assíncrona →
extração de IDs → busca recursiva → geração de relatórios**.

1. **`maigret/maigret.py`** — ponto de entrada do CLI (`run()` → `main()`).
   Faz o parsing de argumentos (`setup_arguments_parser`), carrega settings e a
   base de dados, dispara a busca e aciona a geração de relatórios.

2. **`maigret/settings.py`** — classe `Settings`. Mescla configurações de três
   locais em ordem: `resources/settings.json` (embutido), `~/.maigret/settings.json`
   e `./settings.json` no diretório atual. Controla timeouts, proxies (Tor/I2P),
   recursão, tipos de relatório, autoupdate, bypass de Cloudflare etc.

3. **`maigret/sites.py`** — modelo da base de dados:
   - `MaigretSite`: um site verificável (URL, método de detecção, tags, engine).
   - `MaigretEngine`: template compartilhado por vários sites do mesmo CMS/plataforma.
   - `MaigretDatabase`: carrega/salva a base (`load_from_*`, `save_to_file`),
     ranqueia sites (`ranked_sites_dict`) e produz estatísticas.

4. **`maigret/checking.py`** — núcleo da verificação assíncrona. Função central
   `maigret(...)`. Hierarquia de "checkers" que abstraem o transporte HTTP/DNS:
   - `SimpleAiohttpChecker` / `ProxiedAiohttpChecker` (aiohttp, com/sem proxy)
   - `CurlCffiChecker` (impersonação de browser via curl_cffi)
   - `CloudflareWebgateChecker` (bypass de Cloudflare)
   - `AiodnsDomainResolver` (checagem de domínios)
   `process_site_result`/`make_site_result` interpretam a resposta; `extract_ids_data`
   e `parse_usernames` alimentam a busca recursiva por novos identificadores.

5. **`maigret/extractors.py` / `error_detection.py`** — extração de IDs das
   páginas (via `socid_extractor`) e detecção de erros/bloqueios/CAPTCHA.

6. **`maigret/report.py`** — geração de relatórios em múltiplos formatos: CSV,
   TXT, JSON (simples/ndjson), HTML, PDF, XMind, grafo (`MaigretGraph`) e
   Markdown. Templates em `maigret/resources/*.tpl`.

7. **`maigret/ai.py`** — análise opcional por LLM. Carrega o prompt de
   `resources/ai_prompt.txt`, resolve a chave de API e faz streaming da resposta.

8. **`maigret/db_updater.py`** — autoatualização da base de dados a partir do
   GitHub (uma vez a cada 24h por padrão), com fallback para a base embutida.

9. **`maigret/web/`** — interface web Flask (`app.py` + templates Jinja em
   `templates/`). Roda buscas em background e serve relatórios.

10. **`maigret/submit.py`** — ferramentas para adicionar/validar novos sites na
    base de dados (modo de submissão).

## Módulos auxiliares

- `result.py` / `types.py` — estruturas de resultado (`QueryResultWrapper` etc.).
- `notify.py` — saída no terminal (`QueryNotifyPrint`), progresso e cores.
- `executors.py` — orquestração de execução assíncrona/concorrência.
- `activation.py` — lógica de ativação/headers dinâmicos para alguns sites.
- `permutator.py` — gera variações de nomes de usuário.
- `utils.py` — utilitários diversos.

## Uso como biblioteca

O pacote expõe uma API pública em `maigret/__init__.py`:

```python
import maigret
# maigret.search  -> checking.maigret (busca)
# maigret.cli     -> maigret.main
# MaigretDatabase, MaigretSite, MaigretEngine, Notifier
```

## Testes e qualidade

- Testes: `make test` (pytest, modo asyncio automático — ver `pytest.ini`).
  Fixtures e bases de teste em `tests/` (`db.json`, `local.json`, `conftest.py`).
- Lint: `make lint` (flake8). Formatação: `make format` (black, configurado em
  `pyproject.toml`).
- `make install` instala dependências de produção e dev via Poetry.

## Convenções

- O projeto é totalmente assíncrono — prefira `async`/`await` e respeite os
  limites de concorrência (`max_connections`) e timeouts das settings.
- Não edite `maigret/resources/data.json` à mão para mudanças de comportamento;
  use o modo de submissão (`submit.py`) ou os utilitários em `utils/`.
- `update_sitesmd` (entrypoint) regenera `sites.md` a partir da base.
