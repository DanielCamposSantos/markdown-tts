# Dependencias e reproducibilidade

## Ambiente suportado

O baseline validado e Windows 11 com Python `>=3.12,<3.13`; outras versoes nao
foram declaradas porque nao passaram por instalacao limpa. O hardware validado e
uma NVIDIA RTX 3060 12 GB, com Torch 2.9.1+cu128 usando runtime CUDA 12.8.

## Classificacao auditada

- **runtime-direct:** torch, torchaudio, transformers, huggingface-hub,
  tokenizers, NumPy, SoundFile, FastAPI, Uvicorn, markdown-it-py, Pydantic e
  python-multipart. As dependencias diretas declaradas pelo checkout MOSS e
  exercidas pelo remote code tambem ficam em `runtime.txt`: safetensors, orjson,
  tqdm, PyYAML, einops, SciPy, librosa, tiktoken, psutil, packaging e ninja.
- **runtime-transitive:** filelock, fsspec, Jinja2, networkx, sympy,
  typing-extensions, regex, Starlette, AnyIO, CFFI e demais resolvidas sob as
  constraints. Pins transitivos metodologicamente criticos ficam no lock; o
  restante e validado por `pip check` e sera reavaliado no Release Audit.
- **optional:** faster-whisper, CTranslate2, PyAV, ONNX Runtime, protobuf e
  flatbuffers. ASR permanece desligado por padrao.
- **development/test:** pytest. Ferramentas auxiliares presentes no ambiente mas
  nao requeridas pela suite oficial sao candidatas a auditoria futura, nao a
  remocao nesta fase.
- **external-system:** FFmpeg com libmp3lame e driver NVIDIA compativel com os
  wheels cu128. Nenhuma DLL de sistema e empacotada.
- **vendor/local:** `vendor/MOSS-TTS`, commit
  `934d6826b084c46a0d033402174d5f8ac4ed2519`. O app nao instala esse checkout em
  editable nem o injeta no `sys.path`: ele documenta a fonte local, enquanto o
  runtime carrega o snapshot versionado pelo Transformers com
  `trust_remote_code=True`.

`environment-current.txt` continua sendo evidencia historica. O lock oficial e
`requirements/constraints.txt`, ligado por SHA-256 a
`reproducibility/baseline.json`.

## Instalacao Windows

Use Python 3.12 e execute:

```powershell
.\scripts\setup_windows.ps1
.\scripts\setup_windows.ps1 -InstallAsr
.\scripts\setup_windows.ps1 -InstallAsr -InstallDev
```

Equivalentemente, o passo especial do PyTorch e:

```powershell
python -m pip install torch==2.9.1+cu128 torchaudio==2.9.1+cu128 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements/runtime.txt -c requirements/constraints.txt
```

O setup nao baixa modelos nem altera o PATH global. A voz canonica e seu
manifesto precisam existir. O snapshot MOSS deve estar provisionado no cache do
Hugging Face na revisao registrada; ASR opcional espera o modelo em
`benchmarks/models/faster-whisper-medium`.

No Windows, o backend ASR abre `torch/lib` somente no processo por
`os.add_dll_directory`; nao persiste alteracoes de PATH.

## Identidade dos modelos

O baseline registra revisoes, tamanhos e hashes de configuracoes/tokenizers
pequenos. O peso MOSS de aproximadamente 9,11 GB nao e novamente hashado: sua
revisao Hugging Face e os hashes de `config.json`/`processor_config.json` fornecem
identidade auditavel sem uma leitura cara de varios GB. O medium CTranslate2 tem
aproximadamente 1,53 GB; configuracao, tokenizer e vocabulario possuem hashes.

Uma instalacao limpa completa, inclusive disponibilidade de wheels e modelos
provisionados, pertence ao Final Cleanup / Release Audit posterior a Fase 14.
