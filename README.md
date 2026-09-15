# Markdown TTS

Aplicacao web local que transforma documentos Markdown em narracao em portugues brasileiro usando o modelo `OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5`. O projeto foi concebido como um leitor local para estudo: o usuario cola Markdown, revisa o plano de fala, gera um MP3 e acompanha a leitura com highlight por unidade.

A geracao ocorre localmente depois que o modelo e seus arquivos auxiliares estiverem disponiveis. Nao ha API paga nem servico remoto de TTS no fluxo da aplicacao.

## Estado atual

Este repositorio contem um MVP funcional validado manualmente. O foco atual e preservar a qualidade da voz e estabelecer uma base para robustez, testes, persistencia e melhorias graduais.

O codigo atual e a fonte de verdade para o que esta implementado. A biblioteca local persistente guarda documentos, geracoes, audio, metadata, playback e WAVs por unidade. Regeneracao individual e cancelamento cooperativo usam a mesma fila. A infraestrutura experimental de validacao ASR existe, mas permanece desativada e fora do pipeline TTS.

## Arquitetura

```text
Markdown no navegador
        |
        v
POST /api/plan ou POST /api/generate
        |
        v
markdown-it-py -> MarkdownBlock
        |
        v
Speech Plan -> SpeechUnit
        |
        v
MOSS-TTS Local Transformer v1.5
(Direct TTS, uma unidade por vez)
        |
        v
Audio tokenizer na GPU -> audio PCM por unidade
        |
        v
concatenacao + pausas + timeline
        |
        v
WAV temporario via soundfile -> MP3 via FFmpeg
        |
        v
outputs/<nome>_<job_id>.mp3 + player do navegador
```

### Backend

`web.py` cria uma aplicacao FastAPI, serve a interface e controla o lifecycle de um unico `JobWorker`. `POST /api/generate` enfileira jobs FIFO no SQLite; somente o worker executa TTS, uma geracao pesada por vez. O frontend consulta o estado persistido por polling a cada 500 ms.

O `ModelManager` inicializa o `MossEngine` sob demanda na primeira geracao. Processor e codigos da referencia permanecem em CPU durante a sessao. O mesmo modelo principal BF16 e reutilizado entre jobs, retornando a CPU antes do tokenizer FP32 ir para GPU no decode; ele e liberado apos 300 segundos ocioso.

### Frontend

A interface e HTML, CSS e JavaScript vanilla. Nao ha build de Node, React ou Vue. O navegador oferece:

- textarea, seletor e drag-and-drop para `.md`/`.markdown` UTF-8 de ate 5 MiB;
- preview CommonMark seguro e Speech Plan em tabs separadas, ambos com debounce;
- progresso por fase da geracao;
- biblioteca minima para reabrir uma geracao concluida;
- play/pause, anterior/proxima unidade, +/-10 s, seek, download e velocidade de 0.75x a 2x;
- clique em uma unidade para iniciar a reproducao daquele timestamp;
- highlight e scroll automatico da unidade ativa conforme o audio toca.

A posicao, unidade ativa e velocidade sao persistidas por geracao no SQLite com
throttle de 3 segundos e restauradas sem autoplay. A velocidade altera apenas
`audio.playbackRate`; o arquivo MP3 gerado nao e alterado.

### Validação ASR experimental

`app/validation/` contém contratos independentes de backend, normalização
conservadora, métricas de cobertura/similaridade, classificação provisória e um
harness CPU-only para benchmark. O backend opcional `FasterWhisperAsrEngine` usa
somente modelos locais e import lazy; `ASR_ENABLED` é `False`; nenhum backend
ASR ou retry automático está conectado ao pipeline. O corpus neutro em
`benchmarks/corpus-pt-br.json` permite comparar candidatos posteriormente sem
usar documentos privados.

### Validação ASR opt-in

A integração Faster-Whisper permanece desativada por padrão. Para um teste local,
inicie um novo processo com `MARKDOWN_TTS_ASR_VALIDATION=1`; o modelo precisa
existir em `benchmarks/models/faster-whisper-medium` (ou no caminho indicado por
`MARKDOWN_TTS_ASR_MODEL_PATH`). A configuração congelada é CUDA,
`int8_float16`, português, beam 5, batch unitário e sem VAD.

Quando habilitada, a geração persiste os WAVs de unidade, descarrega efetivamente
o MOSS, valida serialmente e aceita `PASS`/`WARN`. Unidades `FAIL` são corrigidas
em lote por no máximo duas rodadas antes da montagem e publicação. O ASR nunca é
carregado junto com o MOSS na GPU.

## Requisitos

### Hardware validado

O ambiente de referencia e:

- Windows;
- NVIDIA RTX 3060 com 12 GB de VRAM;
- Intel i5-12400F;
- 16 GB de memoria DDR4;
- armazenamento NVMe;
- Python 3.12;
- CUDA funcionando pelo PyTorch.

A qualidade de audio tem prioridade sobre a velocidade. Nos benchmarks atuais, o modelo em BF16 utiliza aproximadamente 7,75 GB de VRAM carregado, com pico observado de aproximadamente 8,1 GB, e RTF proximo de 0,90. O consumo real pode variar conforme versoes, cache e tamanho do documento.

### Software

- Windows 10/11 com driver NVIDIA e CUDA compativeis com a instalacao do PyTorch;
- Python 3.12;
- FFmpeg instalado e disponivel no `PATH`, incluindo o encoder `libmp3lame`;
- dependencias Python compativeis com o snapshot de `environment-current.txt`.

As importacoes diretas da aplicacao exigem, entre outras, `fastapi`, `uvicorn`, `pydantic`, `markdown-it-py`, `numpy`, `soundfile`, `torch` e `transformers`. O MOSS e carregado com `trust_remote_code=True` a partir do identificador configurado em `app/config.py`.

## Instalacao

O repositorio nao possui atualmente um `requirements.txt` ou instalador proprio. `environment-current.txt` e um snapshot diagnostico do ambiente conhecido por funcionar, nao uma especificacao final de dependencias.

Uma preparacao tipica no Windows e:

1. Instale Python 3.12, FFmpeg e o driver NVIDIA/CUDA apropriado.
2. Crie e ative um ambiente virtual:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. Instale uma pilha PyTorch com suporte CUDA compativel com sua maquina e as dependencias da aplicacao. As versoes presentes no ambiente validado podem ser consultadas em `environment-current.txt`.
4. Garanta que `ffmpeg.exe` esteja no `PATH`:

   ```powershell
   ffmpeg -version
   ```

5. Disponibilize a referencia de voz em `voices/narrator_reference.wav`.

A primeira carga do processor/modelo pode baixar os arquivos do Hugging Face conforme o cache local da biblioteca. O README nao baixa o modelo nem executa uma geracao pesada.

## Configuracao

A configuracao central esta em `app/config.py`:

| Opcao | Valor atual | Funcao |
| --- | --- | --- |
| `MODEL_ID` | `OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5` | checkpoint do MOSS |
| `REFERENCE_AUDIO` | `voices/narrator_reference.wav` | voz canonica |
| `LANGUAGE` | `Portuguese` | idioma enviado ao MOSS |
| `SAMPLE_RATE` | `48000` | referencia declarada do pipeline |
| `MAX_NEW_TOKENS` | `1000` | limite de geracao por unidade |
| `AUDIO_TEMPERATURE` | `1.7` | amostragem de audio |
| `AUDIO_TOP_P` | `0.8` | amostragem de audio |
| `AUDIO_TOP_K` | `25` | amostragem de audio |
| `AUDIO_REPETITION_PENALTY` | `1.0` | penalidade de repeticao |
| `BASE_SEED` | `8300` | base para seed por unidade |
| `MP3_BITRATE` | `192k` | bitrate do arquivo final |

### Voz canonica

`voices/narrator_reference.wav` e a referencia PT-BR definitiva do projeto. Ela foi gerada diretamente pelo MOSS-TTS Local Transformer v1.5 em portugues, a partir do candidato `ptbr_narrator_6`, usando a seed original 83 durante a busca da voz.

A seed de busca nao substitui o arquivo de referencia. O engine sempre codifica `narrator_reference.wav` e passa seus codigos ao MOSS com `language="Portuguese"` em cada unidade. Nao sobrescreva, reprocesse, altere pitch ou substitua esse arquivo sem uma decisao explicita.

## Execucao

Com o ambiente virtual preparado:

```powershell
.venv\Scripts\python.exe web.py
```

Ou, no Windows, execute `iniciar.bat`. O servidor escuta em `127.0.0.1:7860` e o script abre o navegador automaticamente apos iniciar.

Acesse manualmente:

```text
http://127.0.0.1:7860
```

O nome informado pelo usuario e sanitizado para o titulo/download. Novas geracoes ficam em `library/<document_id>/<generation_id>/`, com MP3/metadata versionados e WAV FLOAT lossless por SpeechUnit; `library.db` e a fonte de verdade. Geracoes antigas sem WAVs continuam reproduziveis, mas sao read-only para regeneracao granular.

Na regeneracao manual, a seed base e `BASE_SEED + unit.index +
100000 * (nova_revisao - 1)`. Os retries do runaway guard continuam somando seu
offset proprio de 10000 por tentativa; revisao manual e retry permanecem conceitos
separados.

## API local

- `GET /` serve `static/index.html`.
- `POST /api/plan` recebe `{ "markdown": "..." }` e retorna a quantidade de blocos e as unidades com `id`, `kind`, `text`, `previous_id` e `next_id`.
- `POST /api/preview` renderiza apresentacao CommonMark segura, sem participar do TTS.
- `POST /api/generate` recebe `{ "markdown": "...", "filename": "..." }`, persiste um job `queued` e retorna seu `job_id`.
- `GET /api/jobs` lista a fila persistente; `POST /api/jobs/{job_id}/cancel` solicita cancelamento.
- `GET /api/model/status` informa lifecycle e disponibilidade CUDA sem carregar o MOSS.
- `GET /api/jobs/{job_id}` retorna estado, progresso, timeline e, ao concluir, a URL do audio.
- `GET /api/download/{job_id}` envia o MP3 concluido como `audio/mpeg`.
- `GET /audio/<arquivo>` serve diretamente os arquivos em `outputs/`.
- `GET /api/library` lista documentos e geracoes persistidos.
- `GET /api/library/{document_id}` recupera Markdown e geracoes do documento.
- `GET /api/generations/{generation_id}` recupera estado, metadata e timeline.
- `GET /api/generations/{generation_id}/audio` e `/download` servem o audio por ID.
- `GET` e `PUT /api/generations/{generation_id}/playback` restauram e atualizam
  posicao, unidade ativa e velocidade por geracao.
- `POST /api/generations/{generation_id}/units/{unit_id}/regenerate` enfileira
  regeneracao de uma unica SpeechUnit; `/regenerations` retorna o historico.

Os jobs ativos continuam em memoria. Reiniciar perde o polling do job, mas documentos e geracoes concluidas continuam consultaveis pela biblioteca SQLite.

## Pipeline de Markdown e fala

O parser usa `markdown-it-py` no modo CommonMark e preserva blocos narraveis como:

- titulos;
- paragrafos;
- listas ordenadas e nao ordenadas;
- blockquotes;
- blocos de codigo e codigo inline.

Formatacao inline como negrito, italico e links e removida da sintese, enquanto o texto e preservado. Imagens usam o texto alternativo quando disponivel e HTML inline e ignorado.

O Speech Plan converte blocos em `SpeechUnit` imutaveis. Titulos recebem uma pausa de 500 ms e terminacao pontuada quando necessario. Paragrafos sao divididos em frases por pontuacao, com tratamento para abreviacoes conhecidas. Frases de paragrafos recebem pausas de 250 ms; blockquotes, 300 ms; listas e codigo, 500 ms.

Quando um paragrafo terminado em dois-pontos e seguido por uma lista, a introducao e a lista sao combinadas em uma unidade contextual, com pausas MOSS `[pause 0.4s]` e `[pause 0.35s]` entre itens. As unidades tambem podem conter `section_id`, `paragraph_id` e `sentence_index`.

Antes de retornar, o plano valida que o conteudo canonico foi preservado e que os relacionamentos `previous_id`/`next_id` formam links consistentes. Esses relacionamentos servem para estrutura, navegacao e interface. Atualmente eles nao sao usados para passar audio anterior como contexto ao MOSS.

## Geracao e timeline

O card de processamento acompanha fases reais do job via polling: fila,
preparacao, carga do modelo quando necessaria, geracao X/Y, decode, montagem,
exportacao e publicacao. O percentual usa pesos fixos e nunca regride; 100% so
aparece depois que artefatos e SQLite foram publicados. Tempo decorrido vem do
backend. A ETA de geracao, sempre aproximada, surge somente depois de tres
unidades concluidas. Ela usa a mediana recente de segundos por unidade de trabalho,
ponderando palavras e pausas explicitas, e pode permanecer indisponivel (`null`).

Cada unidade e sintetizada de forma independente, sempre retornando a referencia canonica e o idioma `Portuguese`. O fluxo normal nao usa Continuation/context chaining, pois esse experimento causou deriva progressiva de pitch e prosodia.

Antes do decode, um guard conservador estima a duracao pelos frames acusticos do
MOSS (12,5 frames/s). Saidas claramente descontroladas sao descartadas e somente
a unidade afetada e repetida, por no maximo duas vezes, com seeds alternativas
deterministicas. A primeira tentativa e seus parametros permanecem inalterados.

O modelo e carregado em BF16. As saidas de geracao sao movidas para CPU, decodificadas pelo audio tokenizer em FP32 na GPU e convertidas para audio float32 em CPU. As unidades sao concatenadas com silencio entre elas. A timeline registra, para cada unidade, `index`, `kind`, texto, `start_seconds`, `end_seconds` e `pause_after_ms`.

O highlight usa esses timestamps reais do audio produzido; nao estima duracao pelo numero de caracteres. O futuro alinhamento palavra por palavra ainda nao existe.

## Audio no Windows

O pipeline evita `torchaudio.load` e `torchaudio.save`. No ambiente Windows atual, o TorchCodec pode falhar ao carregar `libtorchcodec`. Por isso:

1. `soundfile` le a referencia WAV;
2. o audio tokenizer MOSS codifica/decodifica na GPU quando necessario;
3. `soundfile` escreve um WAV PCM_16 temporario;
4. FFmpeg converte o WAV em MP3 `192k` com `libmp3lame`.

Uma conversao indiscriminada do audio tokenizer inteiro para BF16 tambem nao e assumida: um teste encontrou incompatibilidade entre tipos `float` e bias `BFloat16`. O codigo atual mantem essa parte em FP32.

## Decisoes arquiteturais importantes

- O modelo definitivo e `MOSS-TTS-Local-Transformer-v1.5`; nao substituir por outro motor sem avaliacao e aprovacao explicitas.
- A voz e definida pelo arquivo PT-BR `narrator_reference.wav`, nao pela seed 83 isoladamente e nao por uma referencia gerada em ingles.
- A estrategia e Direct TTS independente por unidade, sem Continuation.
- Dividir paragrafos em frases menores foi mantido para reduzir omissoes, melhorar fluidez e permitir timestamps granulares.
- SQLite e a fonte de verdade da biblioteca; Markdown, MP3, WAVs por unidade e metadata versionada ficam em `library/`.
- A interface permanece sem framework frontend para reduzir a superficie operacional do aplicativo local.

## Limitacoes conhecidas

- CUDA e a referencia de voz sao obrigatorios para a geracao atual; sem CUDA o engine falha ao inicializar.
- E necessario ter FFmpeg no `PATH`.
- Apenas uma geracao pesada ocorre por vez; outras permanecem na fila FIFO.
- Ha cancelamento cooperativo, runaway retry e regeneracao individual para geracoes novas com WAVs por unidade.
- Jobs, fila, regeneracoes e playback sao persistidos em SQLite; jobs ativos interrompidos sao recuperados explicitamente.
- A interface restaura posicao e velocidade, oferece navegacao por unidade e usa a timeline real.
- O perfil controlado `pt-BR-v3`, voltado a estudos de ciberseguranca e redes, soletra em PT-BR apenas siglas cadastradas no lexicon extensivel. Cues foneticos enviados ao MOSS podem diferir da grafia oficial do nome da letra; especificamente, o nome canonico `Y = ípsilon` usa o cue interno `ípsilom`. O texto exibido permanece original e nao ha expansao universal de palavras maiusculas. `C++` mantem o override aprovado `cê mais mais`.
- A aplicacao nao executa validacao ASR automatica; os thresholds experimentais ainda exigem benchmark PT-BR.
- O arquivo `environment-current.txt` registra um ambiente que funcionou, mas as dependencias ainda nao estao congeladas em um manifesto de instalacao do projeto.

## Estrutura de pastas

```text
.
├── app/
│   ├── audio_io.py          # combinacao, timeline e exportacao MP3
│   ├── config.py            # modelo, voz e parametros do pipeline
│   ├── markdown_parser.py   # Markdown -> blocos narraveis
│   ├── moss_engine.py       # processor, geracao e decoder MOSS
│   └── speech_plan.py       # blocos -> SpeechUnit e validacoes
├── static/
│   ├── app.js               # preview, polling, player e highlight
│   ├── index.html           # interface web
│   └── style.css            # estilos da interface
├── voices/
│   └── narrator_reference.wav
├── outputs/                 # MP3s gerados, ignorados pelo Git
├── temp/                    # area temporaria reservada
├── vendor/MOSS-TTS/         # checkout local do projeto MOSS
├── environment-current.txt  # snapshot diagnostico do ambiente
├── iniciar.bat              # inicializacao Windows
└── web.py                   # entrada FastAPI e servidor local
```

## Desenvolvimento

O entrypoint e `web.py`. O frontend e servido diretamente por FastAPI, portanto nao existe etapa de build. Para investigar o parser e o Speech Plan, use entradas pequenas e o endpoint `/api/plan`; isso nao carrega o modelo nem exige uma geracao pesada.

A geracao real exige CUDA, os pesos do MOSS e varios gigabytes de VRAM. Testes futuros de parser, plano, links, serializacao, persistencia e API devem permanecer separados dos testes de inferencia que exigem GPU.

O snapshot funcional do MVP esta registrado no historico Git. Mudancas no modelo, na referencia vocal, no idioma, no modo Direct TTS ou no uso de contexto devem ser tratadas como mudancas de qualidade e avaliadas separadamente.

## Manutencao, integridade e backup

A referencia vocal possui manifesto SHA-256 versionado. Uma referencia ausente,
alterada ou ilegivel bloqueia novas geracoes e regeneracoes, sem impedir consulta,
playback ou download e sem tentar reparo ou voz alternativa.

```powershell
.\.venv\Scripts\python.exe -m app.maintenance retention-scan
.\.venv\Scripts\python.exe -m app.maintenance cleanup-safe
.\.venv\Scripts\python.exe -m app.maintenance cleanup-safe --apply
.\.venv\Scripts\python.exe -m app.maintenance backup
.\.venv\Scripts\python.exe -m app.maintenance verify-backup .\backups\backup-TIMESTAMP
.\.venv\Scripts\python.exe -m app.maintenance restore .\backups\backup-TIMESTAMP --apply
```

Cleanup e dry-run por padrao. Restore valida hashes e SQLite, recusa jobs ativos,
cria backup de seguranca e nao restaura a voz sem `--restore-voice`. Backups
incluem SQLite, artefatos publicados referenciados, documentos, metadata, WAVs de
unidade, voz/manifesto e fontes essenciais de schema/pronuncia; excluem `.venv`,
Git, caches, modelos e corpus/resultados de benchmark.

Documentos, SQLite e publicados sao autoridade; a voz e ativo protegido; staging
conhecido e antigo e descartavel. Fixtures, benchmarks, modelos e ferramentas sao
artefatos de desenvolvimento. Desconhecidos e geracoes legacy sao preservados.

## Ambiente reproduzivel

O suporte validado e Windows 11 com Python `>=3.12,<3.13`. Dependencias diretas,
ASR opcional, desenvolvimento e constraints exatas ficam em `requirements/`;
`environment-current.txt` e apenas evidencia historica. O baseline auditavel esta
em `reproducibility/baseline.json`. Detalhes e classificacao das dependencias:
`docs/DEPENDENCIES.md`.

```powershell
.\scripts\setup_windows.ps1 -InstallDev
.\scripts\setup_windows.ps1 -InstallAsr -InstallDev
.\.venv\Scripts\python.exe -m app.maintenance environment-check
.\.venv\Scripts\python.exe -m app.maintenance environment-check --json
```

O setup usa o indice oficial cu128 para PyTorch, nao baixa MOSS/Faster-Whisper,
nao altera o PATH global e nunca substitui a voz canonica.

## Roadmap

A evolucao planejada deve acontecer em fases pequenas, sempre preservando o comportamento e a qualidade atuais:

1. estabilizacao, testes sem GPU e dependencias reprodutiveis;
2. definicao da persistencia de documentos, audio, metadata e configuracoes;
3. biblioteca local e navegacao completa do player;
4. regeneracao manual e depois seletiva de unidades problematicas;
5. validacao opcional com ASR local, apos avaliar o impacto de VRAM e RAM;
6. cancelamento, filas e estados de processamento mais detalhados;
7. melhorias de Markdown, responsividade e identidade visual;
8. alinhamento palavra por palavra, presets e eventual aplicativo desktop.

Nenhuma dessas etapas deve trocar o motor MOSS ou a estrategia vocal atual sem uma razao explicita e uma validacao de regressao de qualidade.
