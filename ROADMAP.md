# Roadmap de evolucao

Este roadmap transforma o MVP funcional em uma aplicacao local de estudo mais robusta. Cada fase deve ser executada isoladamente, revisada e validada antes da seguinte. O baseline protegido e `cd4ce9f`.

## Principios

- Correcao, qualidade vocal, confiabilidade, UX e depois velocidade.
- Uma unica inferencia TTS pesada por vez na RTX 3060.
- Nenhuma API paga ou servico remoto de TTS.
- MOSS v1.5, voz canonica, Portuguese e Direct TTS independente permanecem fixos.
- Testes sem GPU sao a porta de entrada de cada fase; testes de MOSS ficam separados.
- Toda persistencia deve ser atomica, versionada e recuperavel apos crash.

## Fase 0 - Contrato e caracterizacao do baseline

**Objetivo:** congelar o comportamento atual em testes e registrar limites reais antes de refatorar.

**Motivacao:** o MVP funciona, mas parser, plano, API e audio nao possuem uma suite automatizada. Sem caracterizacao, melhorias podem degradar a voz ou alterar texto narrado sem serem percebidas.

**Dependencias:** nenhuma; requer apenas o ambiente Python atual para testes sem GPU.

**Areas provaveis:** novos testes para `app/markdown_parser.py`, `app/speech_plan.py`, `app/audio_io.py` e `web.py`; configuracao de teste somente se necessaria.

**Tarefas:** cobrir blocos Markdown, inline text, frases, abreviacoes, listas contextuais, links, secoes, IDs e preservacao canonica; testar `combine_audio` com tensores pequenos; testar `safe_filename`, `/api/plan` e erros basicos da API; registrar um teste opt-in para smoke de GPU sem baixar modelo.

**Riscos:** testes acoplados a detalhes semanticos demais podem impedir evolucao legitima; fixtures podem incluir texto pessoal.

**Testes:** suite rapida sem CUDA e sem importacao pesada de MOSS quando possivel; smoke de GPU separado e marcado.

**Aceitacao:** cobertura dos contratos criticos; `pytest` normal nao exige GPU nem pesos; baseline passa sem gerar audio real.

**Rollback:** remover somente a suite nova ou reverter o commit da fase, sem tocar runtime.

**Nao mudar:** modelo, parametros, referencia, idioma, modo Direct TTS, formato MP3 e endpoints existentes.

## Fase 1 - Contratos de dominio e limites de modulo

**Objetivo:** separar modelos/servicos de dominio das preocupacoes de FastAPI sem mudar comportamento.

**Motivacao:** `web.py` concentra API, concorrencia e orquestracao; isso dificulta persistencia e testes.

**Dependencias:** Fase 0.

**Areas provaveis:** novos modulos leves para modelos de documento, unidade, timeline e resultado; adaptadores para parser/planner; `web.py` como camada fina.

**Tarefas:** definir contratos tipados; introduzir `GenerationService` sem alterar o engine; manter adaptadores para estruturas atuais; definir estados de job.

**Riscos:** import cycles, alteracao acidental de pausas ou ordem das unidades.

**Testes:** repetir testes da Fase 0 contra os novos contratos; testes de equivalencia do plano.

**Aceitacao:** API e geracao atual continuam funcionando; cada modulo tem uma responsabilidade clara; nenhuma mudanca de audio.

**Rollback:** manter o caminho antigo atras de adaptadores e remover a camada nova.

**Nao mudar:** algoritmo do parser/plano e implementacao MOSS.

## Fase 2 - Persistencia da biblioteca e metadata

**Status:** implementada. Novas geracoes usam SQLite + filesystem em `library/`,
com manifesto `metadata.json` versionado e publicacao atomica dos artefatos.

**Objetivo:** criar biblioteca local de documentos e geracoes com recuperacao confiavel.

**Motivacao:** jobs e resultados atuais sao efemeros; reiniciar o servidor perde a referencia das geracoes.

**Dependencias:** Fases 0 e 1.

**Areas provaveis:** `persistence/`, schema versionado, repositorios, endpoints de biblioteca, configuracao de data directory.

**Decisao:** usar SQLite como fonte de verdade do estado mutavel e indice transacional, combinado com filesystem para blobs grandes. Cada geracao tera uma pasta por `document_id`/`generation_id`, com `document.md`, `audio.mp3`, WAV opcional e `metadata.json` como manifesto/snapshot versionado e portatil da geracao. Em runtime normal, divergencias entre SQLite, manifest e filesystem sao resolvidas pela autoridade do SQLite; uma rotina futura de reconciliation podera verificar ou reconstruir dados segundo regras explicitas. SQLite facilita filtros, ordenacao, exclusao e recuperacao de jobs; JSON torna o artefato portavel e auditavel. Nao usar filesystem + JSON puro como indice principal porque concorrencia, consultas e estados parciais ficam frageis.

**Tarefas:** definir schema; IDs; estados; timestamps UTC; paths relativos; checksum da referencia; escrita em temp + rename; transacoes; migrations; importacao/limpeza de artefatos orfaos.

**Riscos:** inconsistencias entre SQLite e blobs; migracoes irreversiveis; vazamento de Markdown privado em logs.

**Testes:** round-trip metadata, migration, interrupcao entre etapas simulada, paths invalidos, exclusao segura e consultas concorrentes.

**Aceitacao:** uma geracao concluida pode ser reaberta apos reinicio; nenhuma geracao parcial aparece como concluida; o baseline MP3 continua acessivel.

**Rollback:** feature flag para manter outputs legados somente leitura; migracao reversivel e backup do banco.

**Nao mudar:** formato do audio nem `narrator_reference.wav`.

## Fase 3 - Lifecycle persistente de jobs e fila

**Status:** implementada. Jobs usam fila FIFO SQLite, claim atomico, worker unico,
cancelamento cooperativo, heartbeat e recovery para `interrupted`.

**Objetivo:** substituir threads ad hoc por fila persistente com uma unidade pesada por vez.

**Motivacao:** o lock atual impede concorrencia pesada, mas nao oferece fila, cancelamento ou recuperacao.

**Dependencias:** Fases 1 e 2.

**Areas provaveis:** `jobs/`, `JobQueue`, worker unico, endpoints de status/cancelamento, frontend de fila.

**Decisao:** fila FIFO persistida no SQLite, com prioridade futura explicita e worker unico. Chamadas web continuam paralelas; somente o worker acessa a inferencia pesada. Nao usar paralelismo na RTX 3060.

**Tarefas:** estados `queued`, `running`, `cancelling`, `cancelled`, `failed`, `completed`; lease/heartbeat; idempotencia; reordenacao futura; prioridade sem quebrar FIFO; erros serializaveis.

**Riscos:** job preso apos crash; duas instancias processando o mesmo item; corrida entre cancelamento e conclusao.

**Testes:** transicoes, concorrencia de requisicoes, restart recovery e exatamente um worker.

**Aceitacao:** fila visivel e ordenada; um job ativo; jobs abandonados apos restart sao marcados como `failed`/`interrupted`, nunca silenciosamente concluidos.

**Rollback:** manter endpoint de geracao legado como modo unico; desativar fila nova sem apagar registros.

**Nao mudar:** unidade de sintese, parametros MOSS ou audio anterior como contexto.

### Fase 3.5 - Audio generation runaway guard

**Status:** implementada. Um detector pre-decode conservador usa frames acusticos
para rejeitar falhas de encerramento e repetir somente a SpeechUnit afetada. A
tentativa original permanece inalterada; nao ha ASR nem duration forcing.

## Fase 4 - ModelManager e lifecycle GPU

**Objetivo:** controlar carga, reutilizacao e liberacao do MOSS de forma observavel.

**Motivacao:** carregar/descarregar o modelo custa tempo e a VRAM e limitada.

**Dependencias:** Fases 1 e 3; telemetria de erro precisa existir.

**Areas provaveis:** `tts/ModelManager`, `MossEngine` como adaptador, status de GPU.

**Decisao:** um manager com lock de carga e estado `unloaded/loading/ready/generating/unloading/error`; reutilizar modelo durante uma janela configuravel; descarregar apos timeout ocioso; nunca carregar duas vezes. O tokenizer segue o fluxo FP32 validado.

**Tarefas:** encapsular load/unload, OOM recovery, `empty_cache`, health status, timeout configuravel, metricas opcionais.

**Riscos:** deadlocks, memoria residual, degradacao de qualidade por mudanca de dtype, corrida com cancelamento.

**Testes:** doubles sem CUDA, estados e timeouts; smoke opt-in em RTX 3060; teste de OOM controlado somente em ambiente dedicado.

**Aceitacao:** nenhum carregamento duplicado; erro de CUDA retorna estado recuperavel; audio do baseline permanece equivalente.

**Rollback:** usar adaptador que reproduz carga atual por job e desativar keep-warm.

**Nao mudar:** BF16 do modelo, FP32 do tokenizer, referencia e Direct TTS.

## Fase 5 - Player, retomada e timeline persistente

**Objetivo:** tornar a leitura retomavel e navegavel.

**Motivacao:** timestamps ja existem, mas posicao e velocidade nao sao persistidas no servidor.

**Dependencias:** Fase 2; timeline estavel.

**Areas provaveis:** endpoints de playback, metadata, `static/app.js`, `index.html` e CSS.

**Tarefas:** anterior/proxima unidade usando IDs; +/-10 s; atalhos acessiveis; play/pause; clique e seek; scroll; salvar posicao exata, unidade ativa e velocidade com debounce; restaurar ao reabrir.

**Riscos:** seek para timeline antiga; autoplay bloqueado; conflito entre abas.

**Testes:** unitarios de navegacao/timeline e testes de API; testes manuais de teclado e restart do navegador.

**Aceitacao:** retomar no mesmo documento e audio; playback rate continua somente no player; timeline real dirige highlight.

**Rollback:** esconder controles novos e manter player atual.

**Nao mudar:** timestamps calculados a partir do audio nem MP3 salvo.

## Fase 6 - Regeneracao individual

**Objetivo:** regenerar uma SpeechUnit sem refazer o documento inteiro.

**Motivacao:** uma frase ruim nao deve custar uma nova geracao completa.

**Dependencias:** Fases 1 a 5; armazenamento por unidades ou cache suficiente.

**Areas provaveis:** `GenerationService`, armazenamento de unit audio, API e UI.

**Tarefas:** identificar unidade por ID; usar sempre referencia canonica e Portuguese; gerar apenas a unidade; validar audio; substituir atomically; reconstruir concatenacao e timeline de todas as unidades posteriores; registrar historico de regeneracao e configuracao.

**Riscos:** offsets incorretos, perda de audio antigo, corrida com playback, unidade contextual de lista.

**Testes:** assembler com audio sintetico, offsets antes/depois, rollback de substituicao, erro de FFmpeg e preservacao de Markdown.

**Aceitacao:** somente a unidade solicitada muda; timeline posterior e duracao sao recalculadas; falha deixa a versao anterior intacta.

**Rollback:** manter generation inteira como fallback e nao expor botao se armazenamento por unidade nao estiver pronto.

**Nao mudar:** `display_text`, Markdown original, voz, idioma e ausencia de Continuation.

## Fase 7 - Drag-and-drop e preview Markdown real

**Objetivo:** melhorar entrada e leitura visual sem quebrar o Speech Plan.

**Motivacao:** colar texto funciona, mas arquivos e preview renderizado reduzem friccao.

**Dependencias:** Fase 0 para contratos e, idealmente, Fase 5 para associacao de IDs.

**Areas provaveis:** `static/`, endpoint de upload opcional e renderer seguro.

**Tarefas:** aceitar `.md` por seletor e drag-and-drop; preservar Markdown no editor; renderizar headings, listas, blockquotes, code, emphasis e links com sanitizacao; mapear visualmente unidades sem usar HTML como fonte de sintese.

**Riscos:** XSS, divergencia entre preview e parser, arquivos grandes.

**Testes:** fixtures de Markdown, sanitizacao, encoding UTF-8, drop invalido e associacao unidade/elemento.

**Aceitacao:** preview nao executa HTML perigoso; texto visual e plano continuam coerentes.

**Rollback:** remover preview e manter editor/plano atuais.

**Nao mudar:** parser de sintese sem testes de equivalencia.

## Fase 8 - Progresso detalhado e estimativa

**Objetivo:** expor fases reais e estimativa aproximada ao usuario.

**Motivacao:** progresso atual e agregado e nao distingue todas as etapas.

**Dependencias:** Fase 3; timeline/metadados da Fase 2.

**Areas provaveis:** eventos de job, API, frontend.

**Decisao:** manter polling inicialmente, pois o frontend ja usa esse mecanismo e o ambiente e local. Adicionar SSE somente se a taxa de polling, latencia ou numero de clientes justificar; WebSocket nao e necessario para eventos unidirecionais. Estimativa deve ser rotulada como aproximada e aprender de historico sem afetar TTS.

**Tarefas:** parsing, plan, model, generation X/Y, decode, validation, regeneration, assemble, export, metadata, completed; progresso monotonicamente consistente; ETA opcional.

**Riscos:** porcentagens falsas, jobs antigos sem novos campos, polling excessivo.

**Testes:** schema de eventos, monotonicidade, compatibilidade e estados de erro.

**Aceitacao:** UI mostra fase atual e unidade; nunca afirma conclusao antes dos artefatos atomicos.

**Rollback:** voltar ao payload de polling atual.

**Nao mudar:** algoritmo de porcentagem sem criterio validado e nem inferencia.

## Fase 9 - ASR local e validacao seletiva

**Objetivo:** detectar omissoes, truncamentos e audio anormal localmente.

**Motivacao:** qualidade subjetiva nao detecta todos os erros de frase ou sigla.

**Dependencias:** Fases 1, 2, 3 e 6; estudo de VRAM/RAM antes de escolher modelo.

**Areas provaveis:** `validation/`, interface `AsrEngine`, backend opt-in, metadata.

**Decisao:** definir `AsrEngine` por interface, com Faster-Whisper/Whisper apenas como candidatos de avaliacao. Executar ASR depois de liberar ou deslocar o MOSS, salvo experimento de memoria comprovado. O ASR nao deve ser import obrigatorio do MVP.

**Tarefas:** benchmark PT-BR, memoria, latencia e qualidade; normalizacao de texto (case, acentos, pontuacao, numeros) sem apagar tokens relevantes; score por unidade; regras para vazio/truncado; threshold configuravel; retorno `pass/warn/fail`; regeneracao opt-in.

**Riscos:** falsos positivos, custo de VRAM, comparar texto ingenuamente, loop de regeneracao.

**Testes:** corpus anotado pequeno, casos de siglas, thresholds e doubles do ASR; nenhum download em testes normais.

**Aceitacao:** validacao e desligavel; resultado explica a decisao; regeneracao nunca e infinita e nunca altera texto visual.

**Rollback:** manter somente geracao e registrar validacao como experimental.

**Nao mudar:** motor TTS e pronuncia global.

## Fase 10 - Overrides de pronuncia

**Objetivo:** tratar poucas expressoes comprovadamente problematicas.

**Motivacao:** siglas como SYN, SYN-ACK, ACK, HTTPS e TLS podem falhar ocasionalmente, mas substituicoes agressivas prejudicam naturalidade.

**Dependencias:** Fase 0; evidencia de testes de audio e, preferencialmente, Fase 9.

**Areas provaveis:** camada de preparacao de `synthesis_text`, configuracao e UI opcional.

**Tarefas:** mapear override original -> sintese; desativado por padrao; manter `display_text`; versionar configuracao; avaliar controles nativos do MOSS antes de transliteracao.

**Riscos:** perda de rastreabilidade, alteracao semantica, aplicacao em substring indevida.

**Testes:** display invariavel, match delimitado, opt-in, casos sem override e regressao auditiva.

**Aceitacao:** somente expressoes aprovadas mudam e o documento visual permanece original.

**Rollback:** desligar todos os overrides por configuracao.

**Nao mudar:** dicionario global, voz e modelo.

## Fase 11 - Retencao, backup e integridade

**Objetivo:** controlar armazenamento e proteger a referencia vocal.

**Motivacao:** audio e documentos podem crescer e o ativo vocal nao pode ser perdido.

**Dependencias:** Fase 2.

**Areas provaveis:** `persistence/`, configuracao, diagnostics e UI de exclusao.

**Tarefas:** checksum SHA-256 e identificador da referencia em metadata; validacao nao destrutiva na inicializacao; backup externo documentado; politicas manter tudo/ultimos N; limpeza de temporarios/orfaos; confirmacao antes de apagar; nunca incluir `narrator_reference.wav` na limpeza.

**Riscos:** apagar dados errados, checksum alterado por processo de backup, links quebrados.

**Testes:** dry-run, filtros, arquivo protegido, crash no cleanup e restauracao de backup.

**Aceitacao:** limpeza auditavel e nunca toca o WAV canonico; artefatos incompletos nao entram na biblioteca.

**Rollback:** modo somente relatorio e desativar limpeza automatica.

**Nao mudar:** conteudo do WAV.

## Fase 12 - Reprodutibilidade e compatibilidade

**Objetivo:** congelar o ambiente sem quebrar o snapshot funcional.

**Motivacao:** `environment-current.txt` e diagnostico, nao lock de instalacao; versoes flutuantes podem mudar remote code e audio.

**Dependencias:** Fases 0 e 4; acesso a ambiente validado.

**Areas provaveis:** manifests de dependencias, instrucoes de setup e CI sem GPU.

**Tarefas:** separar dependencias diretas/transitivas; fixar PyTorch/CUDA wheel, Transformers, Hub, markdown-it-py, FastAPI, uvicorn e soundfile; registrar revisao dos remote-code files/MOSS; testar instalacao limpa em Windows; documentar estrategia de atualizacao.

**Riscos:** incompatibilidade de CUDA, TorchCodec, transformers e remote code; impossibilidade de reproduzir pesos cacheados.

**Testes:** smoke sem GPU, instalacao limpa, smoke opt-in com RTX 3060 e comparacao de audio do baseline.

**Aceitacao:** setup documentado reproduz o ambiente alvo sem atualizacao silenciosa.

**Rollback:** manter snapshot atual e adiar novo manifest ate haver ambiente reproduzivel.

**Nao mudar:** dependencias nesta fase de planejamento; nenhum pacote deve ser instalado agora.

## Fase 13 - GPU status, presets e UX

**Objetivo:** oferecer uma experiencia diaria mais clara e acessivel.

**Motivacao:** biblioteca, fila e validacao exigem estados visiveis; a interface atual e funcional, mas minima.

**Dependencias:** Fases 3, 5 e 8.

**Areas provaveis:** `static/`, status API, configuracao de presets.

**Tarefas:** status discreto de GPU/VRAM/modelo com fallback; presets Estudo/Audiobook/Revisao que alterem pausas/player, nunca modelo/voz sem escolha; responsividade, foco, teclado, erros e estados vazios; velocidade padrao configuravel mantendo 0.75x-2x.

**Riscos:** UI virar requisito de telemetria; preset alterar qualidade vocal.

**Testes:** ausencia de metricas, teclado, viewport, acessibilidade basica e preset deterministico.

**Aceitacao:** app funciona sem metricas GPU; playback rate nao altera arquivo; controles sao utilizaveis por teclado.

**Rollback:** ocultar status/presets e manter defaults.

**Nao mudar:** engine TTS por preset.

## Fase 14 - Palavra e desktop futuro

**Objetivo:** preparar extensoes posteriores sem acoplar o MVP.

**Motivacao:** alinhamento palavra a palavra e empacotamento sao desejaveis, mas nao sao pre-requisitos da biblioteca.

**Dependencias:** Fases 5 e 9 para alinhamento; todas as fases de estabilidade antes de empacotar.

**Areas provaveis:** timeline extensivel, alignment adapter, empacotamento Windows.

**Tarefas:** adicionar spans de palavra opcionais ao schema sem quebrar timestamps de unidade; avaliar ASR/alignment; prototipo de executavel/atalho/janela propria somente depois de estabilizar o servidor.

**Riscos:** custo alto, alinhamento impreciso, empacotador escondendo dependencias CUDA.

**Testes:** compatibilidade de metadata, fallback para frase, instalacao em maquina limpa.

**Aceitacao:** unidade continua funcionando sem alinhamento; desktop e opcional e reproduz o fluxo local.

**Rollback:** nao habilitar extensoes e manter o navegador.

**Nao mudar:** contrato de timestamps de frase e pipeline MOSS.

## Ordem recomendada resumida

`0 contratos -> 1 limites -> 2 persistencia -> 3 jobs/fila -> 4 lifecycle GPU -> 5 player/retomada -> 6 regeneracao -> 7 Markdown -> 8 progresso -> 9 ASR -> 10 pronuncia -> 11 retencao -> 12 reproducibilidade -> 13 UX -> 14 alinhamento/desktop`.

A ordem pode ser ajustada somente apos revisar dependencias e criterios da fase anterior. Nao iniciar a Fase 0 sem autorizacao explicita.
