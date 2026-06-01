# godot-project-doctor

[English](README.md) | **한국어**

[![PyPI version](https://img.shields.io/pypi/v/godot-project-doctor?logo=pypi&logoColor=white)](https://pypi.org/project/godot-project-doctor/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
<!-- Downloads badge: re-add once pepy.tech has indexed the package (a day or two after first release):
[![Downloads](https://static.pepy.tech/badge/godot-project-doctor)](https://pepy.tech/project/godot-project-doctor) -->
[![CI](https://github.com/chamcat97/godot-project-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/chamcat97/godot-project-doctor/actions)

[Godot 4](https://godotengine.org/) 프로젝트를 위한 결정론적 CLI 검사 도구입니다.

`godot-project-doctor`는 Godot 프로젝트 폴더를 스캔해 프로젝트 메타데이터와 리소스 참조를 파싱하고, 흔한 문제들을 탐지하여 CI 파이프라인과 AI 에이전트가 활용할 수 있는 사람·기계 친화적 리포트를 생성합니다.

> **AI 도구가 아닙니다.** 순수 정적 분석만 수행합니다 — LLM 호출도, 네트워크 요청도 없습니다. GitHub Actions, 린터, AI 코딩 워크플로에 적합합니다.

---

## 무엇을 탐지하나요

| 분류 | 탐지 항목 |
|---|---|
| **구조** | 순환 의존성, 누락된 메인 씬, 누락된 오토로드 |
| **참조** | 누락된 외부 리소스(씬·스크립트·텍스처), 깨진 `uid://` 경로 |
| **스크립트** | 정의되지 않은 입력 액션, 깨진 시그널 연결, 미사용 스크립트 |
| **에셋** | 미사용 스크립트/오토로드, 미사용 에셋 후보, 과대 텍스처/오디오 |

**v0.8.0 추가 사항**: 완전한 `uid://` 해석(사이드카 + import + 캐시), SARIF 2.1.0 출력, GitHub Action, 설정 기반 심각도 재정의, 미사용 스크립트 탐지.

---

## 설치

```bash
pip install godot-project-doctor
```

선택: 텍스처 크기 검사를 위해 Pillow를 함께 설치합니다.
```bash
pip install "godot-project-doctor[image]"
```

---

## 빠른 시작

```bash
gdoctor scan ./my-game
```

**출력** (text 모드, Windows CP949에서도 안전):
```
ERROR    MISSING_MAIN_SCENE          project.godot: run/main_scene = res://scenes/Main.tscn (file not found)
ERROR    CIRCULAR_DEPENDENCY         scenes/Player.tscn -> scenes/Level.tscn -> scenes/Player.tscn
WARNING  BROKEN_SIGNAL_CONNECTION    scenes/UI.tscn:[connection] signal=pressed method=_on_clicked (not found in target script)
WARNING  UNUSED_SCRIPT               player/old_controller.gd (not referenced by any scene/autoload)
INFO     NO_EXPORT_PRESETS           export_presets.cfg not found

Scanned 12 scenes, 18 scripts. 2 errors, 2 warnings, 1 info.
```

---

## 사용법

### `scan` — 문제 검사

```
gdoctor scan <project_path> [--format text|json|markdown|sarif] [--output <path>]
              [--fail-on error|warning|info|none] [--strict]
              [--config <path>] [--no-config] [--include-addons]
```

```bash
# 사람이 읽기 좋은 터미널 리포트
gdoctor scan ./my-godot-game

# CI 또는 AI 에이전트용 JSON 리포트
gdoctor scan ./my-godot-game --format json --output report.json

# Markdown 리포트
gdoctor scan ./my-godot-game --format markdown --output report.md

# WARNING 이상이면 CI 실패 처리 (기본값: ERROR만)
gdoctor scan ./my-godot-game --strict

# 종료 코드를 항상 0으로 (실패 처리 안 함)
gdoctor scan ./my-godot-game --fail-on none

# res://addons/ 의 서드파티 플러그인 코드까지 검사
gdoctor scan ./my-godot-game --include-addons

# 명시적 설정 파일 사용
gdoctor scan ./my-godot-game --config ci-strict.toml
```

> **`addons/`는 기본적으로 제외됩니다.** `res://addons/`와
> `res://script_templates/`의 서드파티/에디터 코드는 에디터가 로드하거나
> `class_name`으로 참조되어, 검사하면 거짓 양성으로 실제 문제를 묻어버립니다.
> 함께 검사하려면 `--include-addons`를 주거나 `ignore_addons = false`로 설정하세요.

#### 설정 파일

`gdoctor`는 두 곳 중 하나에서 설정을 읽습니다:

- **`pyproject.toml`** — `[tool.gdoctor]` 테이블 아래 (키에 네임스페이스 적용).
- **`.gdoctor.toml`**(프로젝트 루트) 또는 `--config <path>` — 키가 파일
  **루트**에 위치하며 `[tool.gdoctor]` 접두사가 **없습니다**.

모든 설정 파일을 무시하려면 `--no-config`를 사용하세요.

```toml
# pyproject.toml  →  [tool.gdoctor] 아래 네임스페이스
[tool.gdoctor]
large_texture_dim  = 1024          # 1024px 초과 텍스처 경고 (기본 2048)
large_audio_bytes  = 5_242_880     # 5MB 초과 오디오 경고 (기본 10MB)
ignore             = ["assets/vendor/**", "*.tmp.gd"]
ignore_addons      = true          # addons/ & script_templates/ 제외 (기본값)

[tool.gdoctor.severity]
UNUSED_ASSET_CANDIDATE = "info"    # INFO로 강등
NO_EXPORT_PRESETS      = "none"    # 완전히 억제

[[tool.gdoctor.baseline]]
code    = "MISSING_EXT_RESOURCE"
file    = "scenes/legacy/Old.tscn"
message = "External resource not found: res://legacy/old.gd"
```

```toml
# .gdoctor.toml  →  같은 키지만 루트에 ([tool.gdoctor] 없음)
large_texture_dim = 1024
ignore            = ["assets/vendor/**"]

[severity]
UNUSED_ASSET_CANDIDATE = "info"
```

### `graph` — 의존성 그래프

```
gdoctor graph <project_path> [--format text|mermaid] [--output <path>]
```

파싱된 모든 `.tscn`, `.tres`, `.gd` 파일로부터 `source_file → referenced_resource`
방향성 그래프를 생성합니다. Godot 엔진은 실행되지 않습니다.

```bash
# 들여쓰기 텍스트 그래프
gdoctor graph ./my-godot-game

# Mermaid 플로우차트 (GitHub Markdown이나 Mermaid Live에 붙여넣기)
gdoctor graph ./my-godot-game --format mermaid --output graph.md
```

### `context` — AI 친화적 리포트

```
gdoctor context <project_path> [--issue "<자유 서술>"] [--output <path>]
```

ChatGPT, Codex 등 코딩 에이전트에 그대로 붙여넣을 수 있는 자체 완결형 Markdown
문서를 생성합니다. "Suggested Investigation Focus" 섹션은 결정론적 키워드 규칙을
사용하며 외부 API를 호출하지 않습니다.

```bash
gdoctor context ./my-godot-game --issue "game crashes on Android"
gdoctor context ./my-godot-game --issue "missing resource on startup" --output ctx.md
```

---

## 출력 형식 (`scan`)

### `text` (기본값)

심각도별로 그룹화된 ANSI 컬러 터미널 출력. Windows CP949 / GBK 같은 좁은 인코딩
터미널에서는 ASCII 아이콘(`[E]`, `[W]`, `[i]`)으로 자동 대체됩니다.

### `json`

CI 파이프라인과 AI 코드 에이전트를 위한 안정적인 기계 판독용 JSON:

```json
{
  "schema_version": "1.2",
  "project_root": "/path/to/game",
  "summary": { "project_name": "My Game", "main_scene": "res://scenes/Main.tscn" },
  "file_stats": { "scenes": 3, "scripts": 12 },
  "issue_counts": { "ERROR": 1, "WARNING": 2, "INFO": 1 },
  "refs": [
    {
      "source_file": "scenes/Main.tscn",
      "kind": "ext_resource",
      "type": "Script",
      "uid": "uid://abc123",
      "path": "res://player/player.gd",
      "id": "1",
      "resolved_path": null,
      "resolved_via": null
    }
  ],
  "issues": [ { "code": "MISSING_EXT_RESOURCE", "severity": "ERROR" } ]
}
```

**스키마 이력**

| 버전 | 추가된 필드 |
|---|---|
| `1.0` | 최초 |
| `1.1` | `refs[].kind` |
| `1.2` | `refs[].resolved_path`, `refs[].resolved_via` |

`resolved_path`는 `path`가 `.uid` 사이드카, `.import` 파일, `uid_cache.bin`을 통해
해석된 `uid://` 참조일 때 설정됩니다. `resolved_via`는 그에 따라
`"uid_sidecar"`, `"import"`, `"uid_cache"` 중 하나가 됩니다.

### `markdown`

GitHub 이슈나 문서에 적합한 간단한 Markdown 리포트.

### `sarif`

GitHub Code Scanning을 위한 [SARIF 2.1.0](https://sarifweb.azurewebsites.net/):

```bash
gdoctor scan ./my-godot-game --format sarif --output gdoctor.sarif
```

스캔과 업로드를 한 번에 처리하려면 함께 제공되는 **GitHub Action**을 사용하세요:

```yaml
# .github/workflows/godot-doctor.yml
- uses: chamcat97/godot-project-doctor@v0.8.3
  with:
    project-path: .
    fail-on: warning
    upload-sarif: "true"
  permissions:
    security-events: write
```

---

## 검사 항목

| 코드 | 심각도 | 설명 |
|---|---|---|
| `CIRCULAR_DEPENDENCY` | ERROR | 리소스 의존성 그래프에 순환이 존재함 (예: 씬 A → 씬 B → 씬 A) |
| `MISSING_MAIN_SCENE` | ERROR | `project.godot`의 `run/main_scene`이 존재하지 않는 파일을 가리킴 |
| `MISSING_AUTOLOAD` | ERROR | `project.godot`의 오토로드 경로가 존재하지 않음 |
| `MISSING_EXT_RESOURCE` | ERROR | `.tscn`/`.tres`/`.gd` 파일이 존재하지 않는 경로를 참조함 |
| `LARGE_TEXTURE` | WARNING | 래스터 이미지가 2048×2048px를 초과함 (Pillow 필요) |
| `LARGE_AUDIO` | WARNING | 오디오 파일이 10MB보다 큼 |
| `DUPLICATE_UID` | WARNING | 둘 이상의 소스가 서로 다른 파일에 동일한 `uid://`를 주장함 |
| `BROKEN_SIGNAL_CONNECTION` | WARNING | `.tscn` 시그널 연결의 `method`가 대상 노드에 연결된 GDScript에서 발견되지 않음 (상속된 메서드는 검사 안 함 — 거짓 음성 가능) |
| `UNDEFINED_INPUT_ACTION` | WARNING | GDScript가 `Input.is_action_*()`/`get_action_strength()`를 `project.godot`의 `[input]`에 선언되지 않은 이름으로 호출함 (내장 `ui_*` 액션은 제외) |
| `UNUSED_AUTOLOAD` | WARNING | 오토로드 싱글톤 이름이 어떤 `.gd` 파일에서도 참조되지 않음 (`get_node('/root/...')` 접근이나 비-GDScript 코드는 거짓 양성) |
| `UNUSED_ASSET_CANDIDATE` | WARNING | 파싱된 어떤 씬·리소스·스크립트에서도 참조되지 않는 에셋 |
| `UNUSED_SCRIPT` | WARNING | 어떤 씬·리소스·오토로드·`class_name` 사용에서도 참조되지 않는 `.gd` 파일 (`extends`·타입 변수·씬 노드 `type=`은 추적됨; 동적 로드·tool 스크립트는 여전히 거짓 양성일 수 있음) |
| `NO_MAIN_SCENE` | INFO | `run/main_scene`이 설정되지 않음 (라이브러리 프로젝트에서는 의도적일 수 있음) |
| `NO_EXPORT_PRESETS` | INFO | `export_presets.cfg`가 없음 |

> **`uid://` 경로**: `gdoctor`는 세 가지 소스에서 `uid://` 참조를 해석합니다 —
> `*.uid` 사이드카 파일(최우선), `*.import` 파일, `.godot/uid_cache.bin`
> (best-effort 바이너리 파싱, 실패 시 조용히 무시). 해석된 UID는 존재 여부가
> 검사되며 미사용 에셋 후보에서 제외됩니다. 해석할 수 없는 UID는 거짓 양성을
> 방지하기 위해 조용히 건너뜁니다.

> **`UNUSED_ASSET_CANDIDATE` 관련**: 변수 경로를 쓰는 동적 `load()` 호출은
> 정적 분석으로 탐지할 수 없습니다. 그렇게 로드되는 에셋은 여전히 미사용
> 후보로 표시됩니다.

---

## 개발

```bash
# 개발 의존성 설치 (mypy, ruff, pytest 포함)
pip install -e ".[dev]"

# 테스트 실행
pytest

# 린트
ruff check src tests

# 포맷
ruff format src tests

# 타입 체크
mypy src/godot_project_doctor
```

---

## 로드맵

**완료 (v0.2.1 – v0.8.0)**
- ✅ 순환 의존성 탐지
- ✅ `uid://` 해석을 포함한 누락 리소스 탐지 (사이드카, `.import`, `uid_cache.bin`)
- ✅ 설정 기반 심각도 재정의 및 baseline 억제
- ✅ GitHub Code Scanning을 위한 SARIF 2.1.0 출력
- ✅ CI/CD 연동을 위한 GitHub Action
- ✅ 입력 액션 검증 (GDScript `Input.*` ↔ `project.godot` `[input]`)
- ✅ 시그널 연결 검증 (`.tscn` 시그널 → 대상 GDScript 메서드)
- ✅ 미사용 스크립트 및 오토로드 탐지

**향후 계획**
- 바이너리 `.res`/`.scn` 파일 지원 (Godot 바이너리 포맷 파서 필요)
- GDScript 동적 경로 휴리스틱 (문자열 결합 패턴 부분 지원)
- 씬 노드 트리 분석 (고아 노드, 불일치 노드 타입)

---

## 라이선스

MIT — [LICENSE](LICENSE) 참조.
