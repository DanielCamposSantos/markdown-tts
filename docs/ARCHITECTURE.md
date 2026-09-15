# Arquitetura do Markdown TTS

Este documento distingue o que existe hoje (`CURRENT`) do que e recomendado para a evolucao (`TARGET`). Componentes `TARGET` nao estao implementados.

## Objetivos e restricoes

O sistema e um leitor local de Markdown para estudo. A prioridade e, nesta ordem, correcao, qualidade vocal, confiabilidade, UX e velocidade. A plataforma principal e Windows com Python 3.12, RTX 3060 12 GB, 16 GB RAM e CUDA via PyTorch.

O pipeline deve continuar privado e local depois que modelos e dependencias estiverem disponiveis. A inferencia aprovada e `OpenMOSS-Team/MOSS-TTS-Local-Transformer-v1.5`, com `language="Portuguese"`, `voices/narrator_reference.wav` e Direct TTS independente por unidade. Continuation/context chaining e outros motores estao fora do desenho aprovado.

## CURRENT: componentes

```mermaid
flowchart LR
    Browser[HTML CSS JS vanilla] --> API[web.py FastAPI]
    API --> Plan[parse_markdown + build_speech_plan]
    API --> Jobs[fila SQLite]
    Jobs --> Worker[JobWorker unico]
    Worker --> Manager[ModelManager single load e idle unload]
    Manager --> Engine[MossEngine]
    Engine --> Model[MOSS Local Transformer v1.5 em BF16]
    Engine --> Codec[audio tokenizer em FP32 quando necessario]
    Codec --> Assemble[combine_audio + timeline]
    Assemble --> IO[soundfile WAV temporario + FFmpeg MP3]
    IO --> Library[library SQLite + audio.mp3 + metadata.json]
    Outputs --> Browser
```

`web.py` concentra rotas, validacao de entrada, threads, locks, estado de jobs e coordenacao. Os endpoints atuais sao `GET /`, `POST /api/plan`, `POST /api/generate`, `GET /api/jobs/{job_id}`, `GET /api/download/{job_id}` e `GET /audio/<arquivo>`.

`markdown_parser.py` usa `markdown-it-py` em CommonMark e produz `MarkdownBlock` para headings, paragraphs, lists, blockquotes e code. `speech_plan.py` produz `SpeechUnit` imutavel, divide paragrafos em frases, adiciona pausas, trata listas contextuais e valida preservacao de conteudo. `link_units` atribui `previous_id` e `next_id`; `section_id`, `paragraph_id` e `sentence_index` apoiam estrutura e navegacao.

`moss_engine.py` codifica a referencia com `soundfile`, move o tokenizer para CUDA, devolve os codigos para CPU, carrega o modelo em BF16 e gera cada unidade independentemente. Antes do decode, o runaway guard conta frames acusticos, rejeita duracoes claramente implausiveis e repete somente a unidade afetada com seed deterministica. Depois o engine decodifica, preserva WAV FLOAT lossless por unidade, combina e exporta. `audio_io.py` gera `AudioTimelineEntry` com inicio/fim reais e pausa posterior. MP3s e manifests revisionados ficam em `library/`; SQLite aponta para a revisao autoritativa.

O frontend aceita texto colado, seletor ou drop de Markdown UTF-8 ate 5 MiB. `/api/preview` usa markdown-it-py com HTML/imagens desabilitados e links limitados a HTTP, HTTPS, mailto ou relativos; `/api/plan` continua sendo a fonte separada das SpeechUnits. O player usa timestamps reais para highlight, anterior/proxima, +/-10 s e clique na unidade. Posicao, unidade ativa e velocidade 0.75x-2x ficam na tabela `playback_state` e sao restauradas sem autoplay.

## CURRENT: problemas e limites

- Jobs vivem somente em memoria e desaparecem no restart.
- Um worker unico usa claim FIFO atomico, heartbeat e recovery; nao ha paralelismo TTS.
- O ModelManager faz lazy load, serializa geracoes, reutiliza o modelo BF16 por uma janela ociosa e descarrega de forma controlada.
- A biblioteca nao possui ainda reconciliation automatica para artefatos orfaos apos crash.
- WAV por unidade e historico de regeneracao existem para novas generations; legacy continua sem capacidade granular.
- Cancelamento e cooperativo entre unidades; ASR real nao integra o pipeline.
- O progresso e agregado em parsing, generation, decode e export.
- A sincronizacao de playback entre varias abas usa last-write-wins; nao ha realtime.
- Preview visual e Speech Plan sao tabs separadas; o preview nunca alimenta sintese.
- Algumas siglas podem exigir tratamento pontual; nao ha dicionario fonetico.
- `environment-current.txt` e snapshot diagnostico, nao lock de dependencias.
- O `.gitignore` versionado contem texto literal de um bloco PowerShell, aparentando ser uma inconsistência operacional que deve ser corrigida separadamente e com cuidado.

## TARGET: limites recomendados

A evolucao deve usar poucas camadas com contratos claros:

```text
api/              FastAPI, DTOs, auth local inexistente, HTTP e eventos
jobs/             estados, fila, worker unico, cancelamento e recovery
services/         GenerationService e coordenacao de casos de uso
domain/           Document, SpeechUnit, Timeline, Generation, PlaybackState
speech/           MarkdownParser, SpeechPlanner, PronunciationOverrides
              
tts/              MossEngine adapter e ModelManager
 audio/           AudioAssembler, codecs, exportadores e atomicidade
persistence/      SQLite, blobs filesystem, metadata e migrations
validation/       AudioValidator, AsrEngine interface e normalizacao
static/           editor, preview, player, biblioteca e fila
```

Estas pastas sao uma proposta, nao uma ordem para mover arquivos imediatamente. A primeira migracao deve introduzir contratos e adaptadores sem alterar o caminho atual.

## TARGET: fluxo de geracao

```mermaid
sequenceDiagram
    participant UI as Browser
    participant API as FastAPI
    participant Q as JobQueue
    participant S as GenerationService
    participant M as ModelManager
    participant T as MOSS adapter
    participant A as AudioAssembler
    participant R as Repository

    UI->>API: criar generation(document_id)
    API->>Q: enqueue(queued)
    UI->>API: polling de estado
    Q->>S: executar job
    S->>R: salvar estado running
    S->>M: acquire()
    M->>T: load/reuse MOSS
    loop cada SpeechUnit
        S->>T: Direct TTS + referencia + Portuguese
        T-->>S: audio da unidade
        S->>A: acumular unidade e pausa
        S-->>API: progresso real
    end
    S->>A: montar timeline e artefatos
    A->>R: escrever MP3/WAV/metadata atomicamente
    S->>R: completed somente apos validacao dos artefatos
    UI->>API: carregar timeline e audio
```

O worker deve acessar a inferencia pesada exclusivamente por `ModelManager`. Chamadas HTTP, consultas da biblioteca e atualizacoes de playback podem ocorrer em paralelo, mas nao podem carregar um segundo MOSS nem gerar duas unidades pesadas simultaneamente.

## TARGET: dominio e modelos de dados

### Document

Representa o Markdown original e sua identidade: `document_id`, titulo derivado/editavel, `markdown`, `created_at`, `updated_at` e revisao/conteudo hash. O Markdown original deve ser imutavel dentro de uma `Generation`; uma edicao cria nova revisao ou nova geracao claramente relacionada.

### Generation

Representa uma tentativa de audio: `generation_id`, `document_id`, `status`, `created_at`, `completed_at`, configuracao, modelo/revisao, referencia vocal, duracao, paths relativos e resultado de validacao. Estados terminais devem distinguir `completed`, `failed`, `cancelled` e `interrupted`.

### SpeechUnit

Mantem `id`, `kind`, `display_text`, `synthesis_text`, `source_atoms`, pausas e IDs estruturais. `display_text` e o texto visual; `synthesis_text` pode receber override pontual futuro. O planner deve continuar validando preservacao e ordem dos `source_atoms`.

### TimelineEntry

Mantem `unit_id`, `start_seconds`, `end_seconds`, `pause_after_ms`, kind e texto visual. O contrato de frase continua sendo a unidade de highlight. Word-level spans sao uma extensao opcional, nunca requisito para reproduzir o audio.

### PlaybackState

`generation_id`, `position_seconds`, `active_unit_id`, `playback_rate`, `updated_at` e, se necessario, `client_revision`. Atualizacoes devem ser idempotentes e com debounce no frontend.

## TARGET: metadata e persistencia

A escolha recomendada e **SQLite + filesystem**:

- SQLite e a fonte de verdade do estado mutavel: guarda indice, documentos, geracoes, estados de jobs, playback, configuracoes resumidas e referencias a artefatos.
- Filesystem guarda `document.md`, `audio.mp3`, WAV opcional, `metadata.json` e eventualmente audio por unidade para regeneracao.
- `metadata.json` e um manifesto/snapshot versionado e portatil da geracao, nao a fonte autoritativa do estado durante runtime normal.
- SQLite e melhor que JSON como indice para filtros, ordenacao, concorrencia, estados e recovery.
- Em caso de divergencia durante runtime normal, SQLite prevalece. Reconciliation futura pode verificar ou reconstruir dados usando SQLite, manifests e filesystem conforme regras definidas.
- JSON continua util como manifesto portavel e auditavel por geracao; nao deve ser o mecanismo de consulta concorrente.

Estrutura conceitual:

```text
library/
  <document_id>/
    document.md
    <generation_id>/
      audio.mp3
      audio.wav                 # opcional
      metadata.json
      units/                    # opcional, se regeneracao granular for habilitada
        000001.wav
```

O `metadata.json` deve ter `schema_version` e incluir, no minimo:

```json
{
  "schema_version": 2,
  "document_id": "...",
  "generation_id": "...",
  "title": "...",
  "status": "completed",
  "created_at": "...",
  "updated_at": "...",
  "markdown_path": "document.md",
  "audio": {"mp3_path": "audio.mp3", "wav_path": null},
  "model": {"id": "...", "revision": "..."},
  "voice": {"reference_path": "voices/narrator_reference.wav", "sha256": "..."},
  "audio_format": {"sample_rate": 48000, "channels": 2, "mp3_bitrate": "192k"},
  "units": [],
  "timeline": [],
  "playback": {"position_seconds": 0, "active_unit_id": null, "speed": 1},
  "generation_config": {},
  "validation": null,
  "regeneration_history": []
}
```

O schema real deve ser definido na fase de persistencia. Campos sensiveis nao devem ir para logs. Migrations do SQLite devem ser numeradas, forward-only com backup antes da aplicacao e testadas com fixtures de cada versao. O manifesto JSON deve ter sua propria versao e ser escrito depois dos blobs, sem poder promover sozinho uma geracao a `completed`.

## TARGET: jobs, fila e cancelamento

A fila recomendada e FIFO persistida no SQLite, com prioridade como extensao explicita e um worker unico para TTS. `queued -> running -> cancelling -> cancelled` e complementado por `failed`, `interrupted` e `completed`.

Cancelamento e cooperativo: o frontend solicita cancelamento, o worker marca `cancelling`, verifica um token entre unidades e encerra antes da proxima inferencia. O worker deve limpar temporarios e publicar `cancelled` somente depois de confirmar que nenhum artefato final parcial esta apontado como concluido. Se o cancelamento chegar depois da ultima unidade, prevalece uma politica deterministica documentada, preferencialmente concluir se os artefatos atomicos ja foram publicados.

O estado deve ser thread-safe no processo e transacional no SQLite. Um lease/heartbeat evita que dois workers assumam o mesmo job. O servidor pode responder consultas enquanto o worker esta ocupado.

## TARGET: ModelManager e GPU

O manager controla `unloaded`, `loading`, `ready`, `generating`, `unloading` e `error`. Deve possuir lock de carga, referencia a uma unica instancia, timeout de ociosidade e metodo de shutdown. O caminho inicial mais conservador e manter a semantica atual: tokenizer codifica/decodifica em GPU quando necessario, modelo usa BF16, e memoria e liberada explicitamente onde o fluxo atual ja faz isso.

Em CUDA OOM, o job deve falhar de forma explicita, registrar diagnostico sem segredos, tentar liberar memoria de maneira controlada e deixar o manager recuperavel. Nao se deve alterar automaticamente dtype, modelo ou referencia como mecanismo de recovery.

Status de GPU e opcional. Se NVML ou metricas equivalentes nao estiverem disponiveis, a API deve retornar `unknown` e o frontend continuar funcional.

## CURRENT: regeneracao individual

A unidade deve ser regenerada usando o mesmo `display_text`, `synthesis_text` efetivo, modelo, referencia canonica e idioma. O resultado deve ser escrito em temporario; depois o assembler recompila a sequencia inteira a partir das unidades, recalculando offsets da unidade alterada e de todas as posteriores. O MP3 final e metadata sao publicados por rename atomico somente quando completos.

A versao anterior deve permanecer ate a nova versao passar validacao estrutural e, se habilitada, ASR. O Markdown nao muda. Para listas contextuais, o ID representa a unidade de sintese inteira; regeneracao de subitem exige primeiro modelar subunidades, nao editar offsets manualmente.

A migration v4 adiciona `artifact_revision`, operacao/unit ID aos jobs e
`unit_regenerations`. Somente o WAV alvo recebe novo path; os demais continuam
byte-identical. MP3 e metadata novos sao publicados antes de uma unica transaction
trocar os ponteiros SQLite e reconciliar PlaybackState pela unidade ativa.

## CURRENT: infraestrutura ASR experimental (Fase 9A)

O validator depende do Protocol `AsrEngine`, nunca de Whisper/Faster-Whisper. O
pacote isolado possui `DisabledAsrEngine`, fake, resultados tipados, normalizacao
conservadora e scoring por cobertura de tokens, extras, similaridade e truncamento.
Os thresholds sao provisórios e tokens técnicos convertem casos ambiguos em
`warn`, reduzindo falsos positivos antes do benchmark.

`SpeechUnit esperado -> audio da unidade -> ASR local -> normalizacao -> score/diagnostico -> pass|warn|fail`.

O harness percorre corpus local e produz JSON/CSV com tempo, RTF, scores e RAM/VRAM
opcionais. Faster-Whisper possui adapter opt-in para benchmarks; nenhum backend
foi escolhido ou conectado ao pipeline. A Fase 9B mede PT-BR, memoria, VRAM,
latencia e qualidade antes dessa decisão.
`ASR_ENABLED=False`. O adapter opcional Faster-Whisper exige um caminho local e
carrega CTranslate2 sob demanda. No Windows, as DLLs CUDA de `torch/lib` são
expostas apenas ao processo por `os.add_dll_directory`; não há download, retry ou
integração com a geração normal.

Normalizacao pode uniformizar case, acentos, espacos e pontuacao, mas deve preservar palavras relevantes, numeros, siglas e tokens tecnicos. Similaridade de caracteres isolada nao basta: combinar cobertura de tokens, alinhamento aproximado e regras para truncamento/audio vazio. Thresholds devem ser configuraveis, explicaveis e limitados para evitar loops de regeneracao.

### Pronuncia PT-BR controlada

Depois da construcao e validacao do Speech Plan canonico, o resolver `pt-BR-v3`
cria copias de sintese das unidades. Seu lexicon extensivel, case-insensitive e
delimitado prioriza siglas cadastradas de ciberseguranca e redes; cues foneticos
otimizados para o MOSS podem diferir da grafia canonica dos nomes das letras.
Hifen e barra so sao silenciosos em termos compostos explicitamente cadastrados,
e digitos so possuem expansoes controladas. Nao existe regra geral para sequencias
maiusculas. IDs, `display_text`, `source_atoms`, pausas e Markdown permanecem canonicos.
O perfil separa nomes semanticos de cues acusticos: o nome canonico da letra `Y`
permanece `ípsilon`, enquanto o MOSS recebe o cue interno `ípsilom`.
Metadata registra perfil e regras aplicadas sem
duplicar frases. Geracoes antigas sem esse campo sao tratadas como legacy/none.

Quando ASR esta habilitado, apenas unidades transformadas possuem duas formas
esperadas equivalentes: texto canonico e texto efetivo. Cada forma passa pelos
mesmos thresholds, e vence o melhor resultado; isso nao flexibiliza o validator
global nem promove transcricoes incompletas.

### Integração ASR opt-in

`ASR_VALIDATION_ENABLED` permanece falso por padrão. Quando habilitado, o fluxo
persistido é:

`MOSS gera/decode WAVs -> ModelManager.unload -> AsrManager.load -> validação em lote -> AsrManager.unload -> correção MOSS em lote (se necessária) -> revalidação -> assemble/export/publish`.

`AsrManager` possui lifecycle separado. O unload explícito do `ModelManager`
descarta também a instância `MossEngine`, liberando processor/tokenizer da RAM;
startup lazy e idle unload continuam válidos. Cancelamento e erros liberam ambos
os managers em `finally`.

Há no máximo duas rodadas corretivas. Seus namespaces determinísticos são
`BASE_SEED + unit.index + 1_000_001 * round`; o resíduo evita colisão com os
múltiplos de `100_000` da regeneração manual. Retries internos do guard ainda
somam `10_000 * attempt`, e revisões manuais continuam em seu namespace próprio.
`WARN` é publicado sem retry; `FAIL` persistente impede publicação. Na regeneração
manual, a revisão anterior permanece autoritativa até WAV, ASR, montagem, MP3 e
metadata novos estarem prontos.

Metadata nova pode conter `asr_validation` com backend/configuração, resumo,
scores, tokens divergentes, razões e rodadas por unidade. Sua ausência em
gerações antigas significa `not_validated` e não requer migração SQLite.

A fase canônica `asr_validation` ocupa 80–90% do progresso; correções preservam
monotonicidade e a ETA fica nula fora da geração acústica.

## CURRENT: polling, progresso e ETA

O polling de 500 ms consulta o ultimo estado persistido do Job. As fases reais sao
`queued`, `preparing`, `model_loading` quando necessario, `generation`, `decode`,
`assemble`, `export`, `publish` e os estados terminais. Pesos deterministas tornam
o percentual monotonicamente crescente; 100% so e gravado depois da publicacao
atomica e da conclusao SQLite.

A ETA e telemetria opcional e representa somente a geracao restante. Cada unidade
recebe trabalho deterministico por palavras mais 2,5 palavras equivalentes por
segundo de pausa explicita. Depois de tres unidades concluidas, calcula-se a
mediana das ultimas cinco taxas `segundos/trabalho`, multiplicada pelo trabalho
restante e por uma margem conservadora de 1,15. Assim, tamanhos diferentes e um
outlier isolado sao tratados sem falsa precisao. Sem amostra suficiente, durante
regeneracao individual e fora da geracao, `eta_seconds` e `null`. SSE e WebSocket
nao fazem parte deste fluxo.

## CURRENT: frontend

Continuar com JavaScript vanilla enquanto o fluxo couber nele. Separar visualmente editor/preview, biblioteca, fila, reader e player sem obrigar uma build Node. O preview Markdown deve ser seguro e renderizar estrutura visual sem se tornar a fonte do texto de sintese. Cada elemento de leitura deve manter referencia ao `unit_id` e a timeline real.

O player deve persistir velocidade e posicao, oferecer anterior/proxima, +/-10 s, atalhos, foco acessivel, clique em unidade, scroll e fallback quando audio/timeline estiverem indisponiveis. Playback rate nunca deve reexportar o arquivo.

## TARGET: crash recovery e artefatos

Eventos relevantes:

- Navegador fecha: job continua no worker; ao reabrir, biblioteca consulta estado persistido.
- Servidor reinicia durante job: heartbeat expirado marca `interrupted`/`failed`; nunca `completed` sem manifestos e arquivos validos.
- FFmpeg falha: MP3 nao e publicado; WAV temporario e removido ou preservado em diagnostico controlado.
- CUDA OOM: job falha com motivo; manager libera recursos e nao tenta mudar metodologia silenciosamente.
- Windows reinicia: temp incompleto e lock antigo sao detectados por startup reconciliation.
- Arquivo parcial: usar diretório temporario, fsync quando apropriado, rename atomico e checksum/tamanho antes de registrar conclusao.

Uma rotina de reconciliation deve comparar SQLite e filesystem, marcar orfaos e oferecer limpeza segura. `narrator_reference.wav` fica fora de qualquer limpeza automatica.

## TARGET: retencao, integridade e voz

A politica deve ser configuravel: manter tudo, manter ultimos N documentos/geracoes, ou limpeza manual. Exclusao exige confirmacao e deve registrar o que foi removido. Temporarios e artefatos orfaos podem ser limpos por idade, com dry-run inicial.

Na inicializacao, validar existencia, formato basico e SHA-256 esperado da referencia quando configurado. O checksum e o identificador da referencia no metadata, nao um mecanismo para modificar o arquivo. Backups devem ser externos ao ciclo de limpeza e documentados.

## CURRENT: manutencao segura

`app/maintenance/` separa integridade vocal, retencao e backup/restore. A voz e
comparada ao manifesto e validada via `soundfile`. A retencao reconhece somente
staging conhecido, exige 24 horas, ausencia de job ativo e confinamento real na
biblioteca; publicados, legacy, desconhecidos e symlinks sao preservados.

O backup usa a API SQLite e os paths da fotografia do banco para copiar somente
artefatos autoritativos. Seu manifesto registra schema, contagens, identidade da
voz, tamanhos e hashes, e a verificacao executa `PRAGMA quick_check`. Restore e
CLI: verifica, recusa jobs ativos, cria backup de seguranca, prepara em staging e
faz swap com rollback. A voz exige autorizacao separada.

## TARGET: dependencias e testes

`environment-current.txt` deve continuar sendo tratado como evidencia do ambiente atual. Uma futura estrategia de pinning deve separar dependencias diretas da aplicacao, wheel de PyTorch/CUDA, versoes de Transformers/Hugging Face Hub, `markdown-it-py`, FastAPI, Uvicorn e `soundfile`, alem da revisao dos remote-code files MOSS.

A matriz de testes deve ser:

| Classe | Requer GPU/modelo | Exemplos |
| --- | --- | --- |
| Unidade | Nao | parser, frases, planner, links, schema |
| Integracao leve | Nao | SQLite, repositorios, API, jobs com doubles |
| Browser | Nao necessariamente | player, timeline, teclado, preview seguro |
| Smoke TTS | Sim | uma unidade, codec, exportacao |
| Regressao de qualidade | Sim | corpus PT-BR, referencia canonica, comparacao auditiva/ASR |

A suite normal nunca deve baixar modelo. Mudancas no caminho MOSS devem exigir validacao de regressao em hardware de referencia.

## Estrategia de migracao

1. Caracterizar parser, Speech Plan, audio assembler e API sem mudar runtime.
2. Introduzir modelos e servicos por adaptadores, mantendo `web.py` funcionando.
3. Adicionar SQLite/filesystem com importacao ou modo leitura dos outputs legados.
4. Mover jobs para worker unico persistente e adicionar recovery.
5. Encapsular MOSS em ModelManager sem alterar Direct TTS, dtype ou referencia.
6. Persistir timeline/playback e evoluir o player.
7. Adicionar regeneracao somente quando unidades e artefatos forem atomicos.
8. Adicionar ASR opt-in por interface, apos benchmark de recursos.
9. Evoluir preview, retencao, progresso e UX.
10. Somente depois avaliar alinhamento por palavra e aplicativo desktop.

Cada passo deve ter commit pequeno, criterios de aceitacao, teste focado e rollback. O caminho atual permanece disponivel ate o novo caminho reproduzir o comportamento do baseline.
