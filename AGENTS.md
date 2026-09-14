# Instrucoes para agentes

## Visao geral

Markdown TTS e uma aplicacao local para transformar Markdown em audio de estudo. O runtime atual e um servidor FastAPI em `web.py`, com frontend HTML/CSS/JavaScript vanilla em `static/`. O fluxo e:

`Markdown -> markdown-it-py -> SpeechUnit -> MOSS-TTS Local Transformer v1.5 -> audio por unidade -> timeline -> MP3`.

O baseline funcional e o commit `cd4ce9f` (`chore: snapshot working MOSS TTS MVP`). O commit posterior `b90289b` adiciona o README.

## Comandos uteis

- Verificar estado: `git status --short`
- Ver historico: `git log --oneline --decorate -8`
- Iniciar no Windows: `iniciar.bat`
- Iniciar diretamente: `.venv\Scripts\python.exe web.py`
- URL local: `http://127.0.0.1:7860`
- Validar FFmpeg: `ffmpeg -version`
- Consultar plano sem carregar o modelo: `POST /api/plan`

Nao executar geracao TTS durante testes normais. Nao baixar pesos ou instalar pacotes sem autorizacao explicita.

## Arquitetura atual

- `web.py`: FastAPI, endpoints, jobs em memoria, thread daemon, locks e servidor em `127.0.0.1:7860`.
- `app/markdown_parser.py`: Markdown CommonMark para blocos narraveis.
- `app/speech_plan.py`: blocos para `SpeechUnit`, divisao em frases, pausas, validacao de conteudo e links `previous_id`/`next_id`.
- `app/moss_engine.py`: processor, referencia vocal, geracao Direct TTS por unidade, decoder e montagem/exportacao.
- `app/audio_io.py`: audio tensor, silencio, concatenacao, timeline e WAV temporario via `soundfile` para MP3 via FFmpeg.
- `app/config.py`: checkpoint, parametros de amostragem, paths e bitrate.
- `app/persistence/`: SQLite versionado, repositories e artefatos atomicos da biblioteca.
- `static/`: interface e player.

O modelo e carregado sob demanda. O modelo principal usa BF16 na GPU; o audio tokenizer e movido para a GPU quando necessario e permanece em FP32 no fluxo validado. Apenas uma geracao pesada ocorre por vez.

## Guardrails TTS

- Preservar `OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5`.
- Preservar `voices/narrator_reference.wav`; ele e a identidade vocal canonica e nao pode ser sobrescrito, normalizado destrutivamente, alterado, substituido ou recriado apenas pela seed 83.
- Preservar `language="Portuguese"`.
- Preservar Direct TTS independente por unidade.
- Nao usar Continuation/context chaining para fornecer audio anterior ao MOSS.
- `previous_id` e `next_id` sao estrutura de navegacao, nao contexto de sintese.
- Nao reintroduzir Qwen3-TTS, Kokoro, Chatterbox ou OmniVoice.
- Nao voltar a referencia vocal gerada em ingles.
- Nao criar dicionario fonetico global agressivo. Overrides de pronuncia futuros devem ser pequenos, opcionais e aplicados somente a `synthesis_text`, mantendo `display_text`.
- Nao alterar parametros metodologicos do MOSS sem autorizacao e validacao de qualidade.

## Audio e Windows

Nao trocar `soundfile`/FFmpeg por `torchaudio.load`/`torchaudio.save` sem validar o problema conhecido de TorchCodec no Windows. Nao converter todo o audio tokenizer para BF16 sem resolver a incompatibilidade float32/BFloat16 observada. FFmpeg precisa estar no `PATH` e deve produzir MP3 com `libmp3lame`.

## Testes

Testes comuns devem ser rapidos e nao exigir CUDA, modelo, download ou varios GB de VRAM. Cobrir parser, divisao de frases, Speech Plan, preservacao de conteudo, listas, headings, blockquotes, codigo, links, serializacao, persistencia e API.

Testes de MOSS, GPU, codec e qualidade de audio devem ficar separados, explicitamente marcados e ser executados somente em ambiente preparado. Uma mudanca de inferencia exige teste de regressao de audio e comparacao com o baseline.

## Convencoes

- Ler o codigo e os testes proximos antes de editar.
- Manter mudancas pequenas e reversiveis.
- Preservar APIs e comportamento publico quando nao houver motivo para muda-los.
- Usar as abstracoes existentes antes de criar novas.
- Separar dominio, infraestrutura, jobs e API somente quando isso reduzir acoplamento real.
- Usar escrita atomica para artefatos persistentes futuros.
- Tratar SQLite como fonte de verdade; paths persistidos sao relativos a `library/`.
- Nunca marcar uma geracao `completed` antes de audio e metadata validos existirem.
- Nunca registrar tokens, credenciais, caminhos pessoais ou dados sensiveis.
- Nao alterar arquivos do vendor sem necessidade explicita.

## Git e arquivos protegidos

Antes de trabalhar, verificar `git status --short` e o commit baseline. Nao reescrever commits, nao fazer reset destrutivo e nao descartar alteracoes de terceiros. Antes de concluir, usar `git diff --stat`, `git status --short` e confirmar que arquivos de runtime nao foram tocados quando a tarefa for documental.

Tratar `narrator_reference.wav`, `app/*.py`, `static/*`, `web.py`, `iniciar.bat` e `environment-current.txt` como protegidos em tarefas de planejamento. O `.gitignore` atual parece conter texto literal de um comando PowerShell; nao corrigir incidentalmente, mas considerar isso antes de confiar nele para protecao de artefatos.

## Definicao de pronto

Uma mudanca esta pronta quando:

1. preserva o baseline e os guardrails TTS;
2. possui testes adequados ao risco, sem tornar testes normais dependentes de GPU;
3. possui tratamento de erro, cancelamento e recuperacao coerentes quando aplicavel;
4. documenta schema, migracao e comportamento observavel;
5. foi validada com comandos focados;
6. o diff contem somente os arquivos necessarios.

Nao iniciar uma fase do `ROADMAP.md` sem autorizacao explicita do usuario. Nao implementar features durante tarefas de analise ou planejamento.
